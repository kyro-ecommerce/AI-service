import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.evaluation.offline import (
    CatalogAvailability,
    EvaluationInteraction,
    evaluate_model,
    paired_comparison,
    temporal_leave_last_split,
)
from scripts.evaluate_recsys import (
    calculate_average_precision,
    calculate_dcg,
    calculate_idcg,
    calculate_ndcg,
    calculate_precision,
    calculate_recall,
    calculate_hit_rate,
    calculate_catalog_coverage,
    dataset_fingerprint,
    evaluate_recommendation_models,
    build_demo_data,
    run_evaluation,
)


class EvaluationMetricsUnitTest(unittest.TestCase):
    def test_precision_at_k(self) -> None:
        """Verify Precision@K calculation."""
        recs = [1, 2, 3, 4, 5]
        ground_truth = {1, 3, 9}
        # Top 5 has 2 hits (1, 3) -> Precision = 2/5 = 0.40
        p5 = calculate_precision(recs, ground_truth, k=5)
        self.assertEqual(p5, 0.40)

    def test_recall_at_k(self) -> None:
        """Verify Recall@K calculation."""
        recs = [1, 2, 3, 4, 5]
        ground_truth = {1, 3}
        # Top 5 captures all 2 relevant items -> Recall = 2/2 = 1.0
        r5 = calculate_recall(recs, ground_truth, k=5)
        self.assertEqual(r5, 1.0)

    def test_hit_rate_at_k(self) -> None:
        """Verify Hit Rate@K calculation."""
        recs_hit = [10, 20, 30]
        recs_miss = [100, 200, 300]
        ground_truth = {20}

        self.assertEqual(calculate_hit_rate(recs_hit, ground_truth, k=3), 1.0)
        self.assertEqual(calculate_hit_rate(recs_miss, ground_truth, k=3), 0.0)

    def test_ndcg_at_k(self) -> None:
        """Verify NDCG@K calculation with ideal vs non-ideal order."""
        # Perfect ordering: item 1 at rank 1
        perfect_recs = [1, 2, 3, 4, 5]
        ground_truth = {1}
        self.assertEqual(calculate_ndcg(perfect_recs, ground_truth, k=5), 1.0)

        # Item 1 at rank 2 -> lower DCG than rank 1 -> NDCG < 1.0
        shifted_recs = [99, 1, 3, 4, 5]
        ndcg_shifted = calculate_ndcg(shifted_recs, ground_truth, k=5)
        self.assertLess(ndcg_shifted, 1.0)
        self.assertGreater(ndcg_shifted, 0.0)

    def test_catalog_coverage(self) -> None:
        """Verify Catalog Coverage metric."""
        all_recs = [[1, 2], [2, 3]]
        catalog = {1, 2, 3, 4}
        # Recommended 3 out of 4 catalog items -> 3/4 = 0.75
        coverage = calculate_catalog_coverage(all_recs, catalog)
        self.assertEqual(coverage, 0.75)

    def test_average_precision_rewards_earlier_relevant_items(self) -> None:
        truth = {2, 4}
        early = calculate_average_precision([2, 9, 4], truth, k=3)
        late = calculate_average_precision([9, 2, 4], truth, k=3)

        self.assertGreater(early, late)

    def test_temporal_split_uses_only_past_events(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("user:1", product_id, "VIEW", start + timedelta(days=day))
            for day, product_id in enumerate([1, 2, 3])
        ]

        split = temporal_leave_last_split(interactions, min_train_interactions=2)

        self.assertEqual(len(split.cases), 1)
        case = split.cases[0]
        self.assertEqual(case.ground_truth, {3})
        self.assertTrue(all(event.occurred_at <= case.cutoff for event in case.train))
        self.assertNotIn(3, {event.product_id for event in case.train})

    def test_repeated_holdout_is_excluded_instead_of_leaking(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("user:1", product_id, "VIEW", start + timedelta(days=day))
            for day, product_id in enumerate([1, 2, 1])
        ]

        split = temporal_leave_last_split(interactions, min_train_interactions=2)

        self.assertFalse(split.cases)
        self.assertEqual(split.excluded_actors["repeated_holdout_item"], 1)

    def test_evaluator_filters_seen_items_from_model_output(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("user:1", product_id, "VIEW", start + timedelta(days=day))
            for day, product_id in enumerate([1, 2, 3])
        ]
        split = temporal_leave_last_split(interactions, min_train_interactions=2)

        report = evaluate_model(
            split,
            {1, 2, 3, 4},
            lambda _case, _train, _catalog, _k: [1, 2, 3],
            model_name="leaky-model",
            k=3,
        )

        self.assertAlmostEqual(report["metrics"]["precision@k"]["mean"], 1 / 3, places=6)

    def test_global_training_pool_never_contains_future_events(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("early", 1, "VIEW", start),
            EvaluationInteraction("early", 2, "VIEW", start + timedelta(days=1)),
            EvaluationInteraction("early", 3, "VIEW", start + timedelta(days=2)),
            EvaluationInteraction("late", 4, "VIEW", start + timedelta(days=3)),
            EvaluationInteraction("late", 5, "VIEW", start + timedelta(days=4)),
            EvaluationInteraction("late", 6, "VIEW", start + timedelta(days=5)),
        ]
        split = temporal_leave_last_split(interactions, min_train_interactions=2)

        observed_cutoffs: list[tuple[datetime, list[datetime]]] = []

        def auditing_recommender(case, train_pool, _catalog, _k):
            observed_cutoffs.append(
                (case.cutoff, [event.occurred_at for event in train_pool])
            )
            return [3, 6]

        evaluate_model(
            split,
            set(range(1, 7)),
            auditing_recommender,
            model_name="audit",
            k=2,
        )

        self.assertTrue(observed_cutoffs)
        for cutoff, training_times in observed_cutoffs:
            self.assertTrue(all(event_time < cutoff for event_time in training_times))

    def test_short_history_actors_still_contribute_past_training_events(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("eligible", 1, "VIEW", start),
            EvaluationInteraction("short", 9, "VIEW", start + timedelta(hours=1)),
            EvaluationInteraction("eligible", 2, "VIEW", start + timedelta(days=1)),
            EvaluationInteraction("eligible", 3, "VIEW", start + timedelta(days=2)),
        ]
        split = temporal_leave_last_split(interactions, min_train_interactions=2)

        self.assertEqual(len(split.cases), 1)
        self.assertIn(9, {event.product_id for event in split.train_interactions})

    def test_conversion_protocol_holds_out_only_strong_actions(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("user:1", 1, "VIEW", start),
            EvaluationInteraction("user:1", 2, "VIEW", start + timedelta(days=1)),
            EvaluationInteraction("user:1", 3, "ADD_TO_CART", start + timedelta(days=2)),
            EvaluationInteraction("user:1", 4, "VIEW", start + timedelta(days=3)),
        ]

        split = temporal_leave_last_split(
            interactions,
            min_train_interactions=2,
            history_types={"VIEW", "ADD_TO_CART", "PURCHASE"},
            target_types={"ADD_TO_CART", "PURCHASE"},
        )

        self.assertEqual(len(split.cases), 1)
        self.assertEqual(split.cases[0].ground_truth, {3})
        self.assertTrue(
            all(event.occurred_at < split.cases[0].cutoff for event in split.cases[0].train)
        )

    def test_products_unavailable_at_cutoff_are_filtered(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        interactions = [
            EvaluationInteraction("user:1", 1, "VIEW", start),
            EvaluationInteraction("user:1", 2, "VIEW", start + timedelta(days=1)),
            EvaluationInteraction("user:1", 3, "PURCHASE", start + timedelta(days=2)),
        ]
        split = temporal_leave_last_split(interactions, min_train_interactions=2)
        availability = {
            1: CatalogAvailability(start - timedelta(days=1)),
            2: CatalogAvailability(start - timedelta(days=1)),
            3: CatalogAvailability(start - timedelta(days=1)),
            4: CatalogAvailability(start + timedelta(days=3)),
        }

        report = evaluate_model(
            split,
            {1, 2, 3, 4},
            lambda _case, _train, _catalog, _k: [4, 3],
            model_name="future-item-model",
            k=2,
            catalog_availability=availability,
        )

        self.assertEqual(report["metrics"]["hit_rate@k"]["mean"], 1.0)
        self.assertEqual(report["catalog_coverage"], 0.25)

    def test_demo_report_is_explicitly_non_publishable(self) -> None:
        report = evaluate_recommendation_models(k=3, demo=True, seed=7)

        self.assertEqual(report["dataset"]["source"], "demo_synthetic")
        self.assertFalse(report["evidence"]["publishable"])
        self.assertEqual(report["evidence"]["grade"], "exploratory_only")
        self.assertTrue(report["dataset"]["fingerprint"].startswith("sha256:"))
        self.assertIn("next_item_unseen", report["protocol_results"])
        self.assertIn("conversion_unseen", report["protocol_results"])

    def test_dataset_fingerprint_is_order_independent(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = EvaluationInteraction("user:1", 1, "VIEW", now)
        second = EvaluationInteraction("user:1", 2, "PURCHASE", now + timedelta(days=1))

        self.assertEqual(
            dataset_fingerprint([first, second], {1, 2}),
            dataset_fingerprint([second, first], {2, 1}),
        )

    def test_zero_hits_have_nonzero_wilson_upper_bound(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            EvaluationInteraction("actor", pid, "VIEW", start + timedelta(days=pid))
            for pid in [1, 2, 3]
        ]
        split = temporal_leave_last_split(rows)
        report = evaluate_model(split, {1, 2, 3, 4}, lambda *_args: [4], model_name="miss", k=1)
        metric = report["metrics"]["hit_rate@k"]
        self.assertEqual(metric["mean"], 0)
        self.assertGreater(metric["ci95"][1], 0)
        self.assertEqual(metric["ci_method"], "wilson")

    def test_pairing_detects_matching_and_mismatched_cases(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            EvaluationInteraction("actor", pid, "VIEW", start + timedelta(days=pid))
            for pid in [1, 2, 3]
        ]
        split = temporal_leave_last_split(rows)
        baseline = evaluate_model(
            split, {1, 2, 3, 4}, lambda *_args: [4], model_name="base", k=1, include_per_actor=True,
        )
        candidate = evaluate_model(
            split, {1, 2, 3, 4}, lambda *_args: [3], model_name="candidate", k=1, include_per_actor=True,
        )
        comparison = paired_comparison(candidate, baseline)
        self.assertEqual(comparison["metrics"]["ndcg@k"]["mean_delta"], 1.0)
        self.assertFalse(comparison["claim_ready"])
        candidate["per_actor"]["actor"]["ground_truth"] = [99]
        with self.assertRaises(ValueError):
            paired_comparison(candidate, baseline)

    def test_demo_compares_all_models_without_exposing_actor_ids(self) -> None:
        report = evaluate_recommendation_models(k=3, demo=True)
        for protocol in report["protocol_results"].values():
            self.assertEqual(set(protocol["models"]), {
                "popularity_baseline", "item_knn", "hybrid_item_knn_popularity",
            })
            self.assertEqual(len(protocol["paired_comparisons"]), 2)
            for result in protocol["models"].values():
                self.assertNotIn("per_actor", result)

    def test_postgresql_label_alone_does_not_establish_evaluation_provenance(self) -> None:
        interactions, catalog, availability, audit = build_demo_data()
        audit["source"] = "postgresql"
        report = run_evaluation(interactions, catalog, audit, catalog_availability=availability)

        self.assertEqual(report["evidence"]["grade"], "provenance_unverified")
        self.assertFalse(report["evidence"]["claim_ready"])

    def test_mixed_synthetic_postgresql_data_remains_exploratory(self) -> None:
        interactions, catalog, availability, audit = build_demo_data()
        audit["source"] = "postgresql"
        audit["provenance"] = {"status": "synthetic_or_mixed", "verified_observational": False}
        report = run_evaluation(interactions, catalog, audit, catalog_availability=availability)

        self.assertEqual(report["evidence"]["grade"], "exploratory_only")

    @patch("scripts.evaluate_recsys.load_real_evaluation_data", side_effect=RuntimeError("db down"))
    def test_real_mode_does_not_silently_fallback_to_synthetic(self, _mock_load) -> None:
        report = evaluate_recommendation_models(k=5)

        self.assertEqual(report["status"], "data_source_unavailable")
        self.assertEqual(report["dataset"]["source"], "postgresql")
        self.assertFalse(report["evidence"]["publishable"])


if __name__ == "__main__":
    unittest.main()
