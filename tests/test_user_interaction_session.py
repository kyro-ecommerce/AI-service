import unittest
from unittest.mock import MagicMock, patch

from app.repositories.user_interaction_repository import record_user_interaction_in_new_session
from app.services.chat_common import _load_chat_catalog_snapshot


class UserInteractionSessionTest(unittest.TestCase):
    @patch("app.repositories.user_interaction_repository.record_user_interaction")
    @patch("app.repositories.user_interaction_repository.SessionLocal")
    def test_worker_owns_and_closes_its_database_session(
        self,
        mock_session_factory: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        worker_session = MagicMock(name="worker_session")
        mock_session_factory.return_value.__enter__.return_value = worker_session
        mock_record.return_value = True

        result = record_user_interaction_in_new_session(
            user_id=42,
            interaction_type="CHAT",
            query_text="Laptop 16GB",
            category_intents={"laptop"},
        )

        self.assertTrue(result)
        mock_session_factory.assert_called_once_with()
        mock_session_factory.return_value.__exit__.assert_called_once()
        mock_record.assert_called_once_with(
            db=worker_session,
            user_id=42,
            interaction_type="CHAT",
            query_text="Laptop 16GB",
            category_intents={"laptop"},
            dedup_window_seconds=10,
            product_id=None,
            session_id=None,
            event_id=None,
            occurred_at=None,
            context_data=None,
            request_id=None,
            recommendation_id=None,
            position=None,
            model_version=None,
        )

    @patch("app.services.chat_common.list_active_products_safe", return_value=([], "database"))
    @patch("app.services.chat_common.SessionLocal")
    def test_retrieval_worker_owns_database_session(
        self,
        mock_session_factory: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        worker_session = mock_session_factory.return_value.__enter__.return_value

        products, source = _load_chat_catalog_snapshot()

        mock_list.assert_called_once_with(worker_session)
        self.assertEqual((products, source), ([], "database"))
        mock_session_factory.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
