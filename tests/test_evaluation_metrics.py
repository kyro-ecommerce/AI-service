import unittest
from scripts.evaluate_recsys import (
    calculate_dcg,
    calculate_idcg,
    calculate_ndcg,
    calculate_precision,
    calculate_recall,
    calculate_hit_rate,
    calculate_catalog_coverage,
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


if __name__ == "__main__":
    unittest.main()
