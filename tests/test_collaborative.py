import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.schemas.product import Product
from app.services.recommendation.collaborative import (
    calculate_time_decay,
    compute_user_collaborative_scores,
    INTERACTION_WEIGHTS,
)
from app.services.recommendation_service import recommend_personalized_products


class CollaborativeFilteringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.laptop = Product(
            product_id=1,
            title="Laptop ThinkPad X1 Carbon",
            category_name="laptop",
            brand="Lenovo",
            average_rating=4.8,
            quantity_sold=50,
            is_active=True,
        )
        self.mouse = Product(
            product_id=2,
            title="Chuột Logitech MX Master 3S",
            category_name="mouse",
            brand="Logitech",
            average_rating=4.9,
            quantity_sold=150,
            is_active=True,
        )
        self.products = [self.laptop, self.mouse]

    def test_interaction_weights_values(self) -> None:
        """Verify business weights for implicit interactions."""
        self.assertEqual(INTERACTION_WEIGHTS["VIEW"], 1.0)
        self.assertEqual(INTERACTION_WEIGHTS["SEARCH"], 2.0)
        self.assertEqual(INTERACTION_WEIGHTS["ADD_TO_CART"], 3.5)
        self.assertEqual(INTERACTION_WEIGHTS["PURCHASE"], 5.0)

    def test_calculate_time_decay(self) -> None:
        """Verify exponential time decay halving every 7 days."""
        now = datetime.now(timezone.utc)

        decay_now = calculate_time_decay(now, now)
        self.assertAlmostEqual(decay_now, 1.0, places=3)

        decay_7d = calculate_time_decay(now - timedelta(days=7), now)
        self.assertAlmostEqual(decay_7d, 0.5, places=2)

        decay_14d = calculate_time_decay(now - timedelta(days=14), now)
        self.assertAlmostEqual(decay_14d, 0.25, places=2)

    @patch("app.repositories.user_interaction_repository.get_user_recent_intents")
    def test_compute_user_collaborative_scores(self, mock_get_intents: MagicMock) -> None:
        """Verify collaborative score calculation for active user."""
        mock_get_intents.return_value = {"laptop"}

        now = datetime.now(timezone.utc)
        mock_record = MagicMock()
        mock_record.interaction_type = "PURCHASE"
        mock_record.created_at = now - timedelta(hours=1)
        mock_record.category_intents = ["laptop"]

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = [mock_record]

        cf_scores = compute_user_collaborative_scores(user_id=10, products=self.products, db=mock_db)

        self.assertIn(1, cf_scores)  # Laptop product ID 1 must be scored
        self.assertGreater(cf_scores[1], 0.0)

    @patch("app.services.recommendation.collaborative.compute_user_collaborative_scores")
    @patch("app.repositories.user_interaction_repository.get_user_recent_intents")
    def test_personalized_recommendations_cf_hybrid_strategy(
        self, mock_get_intents: MagicMock, mock_compute_cf: MagicMock
    ) -> None:
        """Verify that recommend_personalized_products uses implicit_collaborative_filtering_hybrid when CF scores are present."""
        mock_get_intents.return_value = {"laptop"}
        mock_compute_cf.return_value = {1: 0.95}

        res = recommend_personalized_products(
            products=self.products,
            user_id=10,
            limit=2,
            db=MagicMock(),
        )

        self.assertEqual(res.strategy, "implicit_collaborative_filtering_hybrid")
        self.assertEqual(res.recommendations[0].product_id, 1)
        self.assertIn("lọc cộng tác", res.recommendations[0].reason)


if __name__ == "__main__":
    unittest.main()
