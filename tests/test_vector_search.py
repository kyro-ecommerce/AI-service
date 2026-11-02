import unittest
from unittest.mock import patch

from app.schemas.product import Product
from app.services.embedding_service import generate_embedding, VECTOR_DIMENSION
from app.services.search_service import calculate_cosine_similarity, search_products
from app.services import embedding_service


class _ControlledEncoder:
    """Deterministic axes for code-path checks, not a semantic-quality benchmark."""

    def encode(self, text, **kwargs):
        normalized = text.casefold()
        axis = (
            2
            if any(word in normalized for word in ("chuột", "mouse"))
            else 1
            if any(word in normalized for word in ("headphone", "airpods", "tai nghe"))
            else 0
        )
        vector = [0.0] * VECTOR_DIMENSION
        vector[axis] = 1.0
        return vector


class VectorSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        embedding_service._generate_embedding_cached.cache_clear()
        # Fictitious pinned profile for offline code-path tests, not a real model.
        profile = embedding_service.EncoderProfile("fixture/encoder", "a" * 40, 384, True)
        for patcher in (
            patch.object(embedding_service.config, "EMBEDDING_MODEL_NAME", profile.model_name),
            patch.object(
                embedding_service.config, "EMBEDDING_MODEL_REVISION", profile.model_revision
            ),
            patch(
                "app.services.embedding_service.get_embedding_model",
                return_value=embedding_service._LoadedEncoder(profile, _ControlledEncoder()),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(embedding_service._generate_embedding_cached.cache_clear)

    def test_generate_embedding_returns_384d_vector(self) -> None:
        text = "Laptop Lenovo IdeaPad Slim 5 16GB RAM 512GB SSD"
        vec = generate_embedding(text)

        self.assertEqual(len(vec), VECTOR_DIMENSION)
        self.assertIsInstance(vec[0], float)

    def test_cosine_similarity_identical_vectors(self) -> None:
        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]

        sim = calculate_cosine_similarity(v1, v2)

        self.assertAlmostEqual(sim, 1.0, places=4)

    def test_hybrid_search_with_controlled_encoder(self) -> None:
        product1 = Product(
            product_id=1,
            title="Lenovo IdeaPad Slim 5",
            category_name="laptop",
            brand="Lenovo",
            description="May tinh xach tay mong nhe cho sinh vien va lap trinh",
            ram_capacity="16GB",
        )
        product2 = Product(
            product_id=2,
            title="AirPods Pro 2",
            category_name="headphone",
            brand="Apple",
            description="Tai nghe khong day chong on",
        )

        # Controlled encoder verifies the retrieval path, not real NLP quality.
        results = search_products([product1, product2], "may tinh hoc lap trinh", limit=5)

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].product_id, 1)

    def test_rrf_category_intent_guardrail(self) -> None:
        mouse_product = Product(
            product_id=10,
            title="Chuột không dây Logitech MX Master 3S",
            category_name="mouse",
            brand="Logitech",
            description="Chuột máy tính yên tĩnh chống ồn cao cấp",
        )
        headphone_product = Product(
            product_id=11,
            title="Tai nghe Sony WH-1000XM5",
            category_name="headphone",
            brand="Sony",
            description="Tai nghe chống ồn chủ động đỉnh cao",
        )

        # Searching for 'chuột chống ồn' MUST prioritize mouse despite 'chống ồn' matching headphone description
        results = search_products([mouse_product, headphone_product], "chuột chống ồn", limit=5)

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].product_id, 10)
        self.assertGreater(results[0].score, 0.0)


if __name__ == "__main__":
    unittest.main()
