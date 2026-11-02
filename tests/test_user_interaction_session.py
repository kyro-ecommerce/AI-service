import unittest
from unittest.mock import MagicMock, patch

from app.repositories.user_interaction_repository import record_user_interaction_in_new_session
from app.services.chat_service import (
    _get_recent_intents_in_new_session,
    _load_chat_catalog_snapshot,
)


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

    @patch("app.services.chat_service.search_products", return_value=[])
    @patch("app.services.chat_service.list_active_products_safe", return_value=([], "database"))
    @patch("app.services.chat_service.SessionLocal")
    def test_retrieval_worker_owns_database_session(
        self,
        mock_session_factory: MagicMock,
        mock_list: MagicMock,
        mock_search: MagicMock,
    ) -> None:
        worker_session = mock_session_factory.return_value.__enter__.return_value

        products, source = _load_chat_catalog_snapshot()

        mock_list.assert_called_once_with(worker_session)
        self.assertEqual((products, source), ([], "database"))
        mock_search.assert_not_called()
        mock_session_factory.return_value.__exit__.assert_called_once()

    @patch("app.repositories.user_interaction_repository.get_user_recent_intents")
    @patch("app.services.chat_service.SessionLocal")
    def test_personalization_worker_owns_database_session(
        self,
        mock_session_factory: MagicMock,
        mock_get_intents: MagicMock,
    ) -> None:
        worker_session = mock_session_factory.return_value.__enter__.return_value
        mock_get_intents.return_value = {"laptop"}

        result = _get_recent_intents_in_new_session(42)

        self.assertEqual(result, {"laptop"})
        mock_get_intents.assert_called_once_with(worker_session, 42)


if __name__ == "__main__":
    unittest.main()
