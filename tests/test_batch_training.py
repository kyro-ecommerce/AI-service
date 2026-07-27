import os
import unittest
from unittest.mock import patch
from app.schemas.product import Product
from app.services.recommendation.collaborative import compute_user_collaborative_scores, load_batch_feature_store
from scripts.train_batch_collaborative import run_batch_training, FEATURE_STORE_PATH


class BatchTrainingUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.laptop = Product(
            product_id=1,
            title="Lenovo ThinkPad X1 Carbon",
            category_name="laptop",
            brand="Lenovo",
            is_active=True,
        )
        self.products = [self.laptop]

    def test_run_batch_training_exports_feature_store(self) -> None:
        """Verify that running batch training generates valid Feature Store JSON."""
        payload = run_batch_training()

        self.assertIn("metadata", payload)
        self.assertIn("user_features", payload)
        self.assertTrue(os.path.exists(FEATURE_STORE_PATH))

        # Verify load_batch_feature_store reads exported feature file
        user_feat = load_batch_feature_store(101)
        self.assertIsNotNone(user_feat)

    def test_compute_user_collaborative_scores_uses_feature_store(self) -> None:
        """Verify that compute_user_collaborative_scores uses Feature Store for < 1ms scoring."""
        # Train feature store first
        run_batch_training()

        cf_scores = compute_user_collaborative_scores(user_id=105, products=self.products)
        self.assertIn(1, cf_scores)
        self.assertGreater(cf_scores[1], 0.0)


    @patch("app.services.recommendation.collaborative.load_batch_feature_store")
    def test_fallback_when_feature_store_missing(self, mock_load_fs) -> None:
        """Verify graceful fallback to real-time SVD / zero scores when Feature Store returns None."""
        mock_load_fs.return_value = None

        cf_scores = compute_user_collaborative_scores(user_id=9999, products=self.products, db=None)
        self.assertEqual(cf_scores, {})


if __name__ == "__main__":
    unittest.main()
