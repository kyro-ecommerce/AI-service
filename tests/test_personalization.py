import unittest
from unittest.mock import MagicMock, patch

from app.schemas.product import Product
from app.services.recommendation_service import recommend_personalized_products


class PersonalizationUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_products = [
            Product(
                product_id=1,
                title="Laptop Dell XPS 15 16GB",
                category_name="laptop",
                brand="Dell",
                original_price=30000000,
                average_rating=4.8,
                quantity_sold=100,
                is_active=True,
            ),
            Product(
                product_id=2,
                title="Chuột Không dây Logitech MX",
                category_name="mouse",
                brand="Logitech",
                original_price=2000000,
                average_rating=4.9,
                quantity_sold=500,
                is_active=True,
            ),
            Product(
                product_id=3,
                title="Tai nghe Sony WH-1000XM5",
                category_name="headphone",
                brand="Sony",
                original_price=8000000,
                average_rating=4.7,
                quantity_sold=200,
                is_active=True,
            ),
        ]

    @patch("app.repositories.user_interaction_repository.get_user_recent_intents")
    def test_personalized_recommendations_with_history(self, mock_get_intents: MagicMock) -> None:
        """Verify that products matching user's recent intent ('laptop') receive personalized score boost."""
        mock_get_intents.return_value = {"laptop"}

        response = recommend_personalized_products(
            products=self.mock_products,
            user_id=123,
            limit=3,
            db=MagicMock(),
        )

        self.assertEqual(response.strategy, "personalized_interaction_profile_boost")
        self.assertTrue(len(response.recommendations) > 0)
        # Top recommendation should be Laptop Dell XPS 15 due to 1.35x personalized score boost
        self.assertEqual(response.recommendations[0].product_id, 1)
        self.assertIn("Gợi ý cá nhân hóa", response.recommendations[0].reason)

    @patch("app.repositories.user_interaction_repository.get_user_recent_intents")
    def test_personalized_recommendations_fallback_no_history(self, mock_get_intents: MagicMock) -> None:
        """Verify that users with zero history fallback gracefully to cold-start trending strategy."""
        mock_get_intents.return_value = set()

        response = recommend_personalized_products(
            products=self.mock_products,
            user_id=999,
            limit=3,
            db=MagicMock(),
        )

        self.assertEqual(response.strategy, "cold_start_fallback_no_history")
        self.assertTrue(len(response.recommendations) > 0)

    def test_chatbot_data_3day_expiry_rule(self) -> None:
        """Verify that Chatbot interactions created > 3 days ago are permanently expired and ignored."""
        from datetime import datetime, timedelta, timezone
        from app.db.models import UserInteraction
        from app.repositories.user_interaction_repository import get_user_recent_intents

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)

        old_chat_record = UserInteraction(
            user_id=55,
            interaction_type="CHAT",
            query_text="Cần mua laptop",
            category_intents=["laptop"],
            created_at=now - timedelta(days=4), # 4 days ago -> Expired!
        )

        new_search_record = UserInteraction(
            user_id=55,
            interaction_type="SEARCH",
            query_text="chuột bluetooth",
            category_intents=["mouse"],
            created_at=now - timedelta(hours=2), # 2 hours ago -> Valid
        )

        mock_db.scalars.return_value.all.return_value = [old_chat_record, new_search_record]

        intents = get_user_recent_intents(mock_db, user_id=55, chat_ttl_days=3)
        self.assertNotIn("laptop", intents) # 4-day old chat intent MUST be expired & excluded
        self.assertIn("mouse", intents)    # Recent search intent MUST be included


if __name__ == "__main__":
    unittest.main()

