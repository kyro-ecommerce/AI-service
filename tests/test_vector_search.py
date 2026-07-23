import unittest

from app.schemas.product import Product
from app.services.embedding_service import generate_embedding, VECTOR_DIMENSION
from app.services.search_service import calculate_cosine_similarity, search_products


class VectorSearchTest(unittest.TestCase):
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

    def test_hybrid_search_semantic_matching(self) -> None:
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

        # Natural language query without exact keyword matches for title
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

