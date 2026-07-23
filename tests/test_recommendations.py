import unittest

from app.schemas.product import Product
from app.services.recommendation_service import (
    recommend_accessories,
    recommend_similar_products,
)


class RecommendationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.laptop1 = Product(
            product_id=1,
            title="Lenovo IdeaPad Slim 5",
            category_name="laptop",
            brand="Lenovo",
            description="Laptop mong nhe cho sinh vien va lap trinh",
            ram_capacity="16GB",
        )
        self.laptop2 = Product(
            product_id=2,
            title="Asus TUF Gaming A15",
            category_name="laptop",
            brand="Asus",
            description="Laptop gaming cau hinh cao",
            ram_capacity="16GB",
        )
        self.mouse1 = Product(
            product_id=3,
            title="Logitech MX Master 3S",
            category_name="mouse",
            brand="Logitech",
            description="Chuot khong day cao cap cho lap trinh va van phong",
            average_rating=4.9,
        )
        self.headphone1 = Product(
            product_id=4,
            title="Sony WH-1000XM5",
            category_name="headphone",
            brand="Sony",
            description="Tai nghe chong on cao cap",
            average_rating=4.8,
        )
        self.products = [self.laptop1, self.laptop2, self.mouse1, self.headphone1]

    def test_recommend_similar_products_returns_laptops_for_laptop(self) -> None:
        res = recommend_similar_products(self.products, target_product_id=1, limit=5)

        self.assertIsNotNone(res)
        self.assertEqual(res.target_product_id, 1)
        self.assertGreater(len(res.recommendations), 0)

        # First recommendation should be laptop2 (Asus TUF Gaming)
        first_rec = res.recommendations[0]
        self.assertEqual(first_rec.product_id, 2)
        self.assertEqual(first_rec.category_name, "laptop")

    def test_recommend_accessories_returns_mouse_and_headphone_for_laptop(self) -> None:
        res = recommend_accessories(self.products, target_product_id=1, limit=5)

        self.assertIsNotNone(res)
        self.assertEqual(res.target_product_id, 1)
        self.assertGreaterEqual(len(res.recommendations), 2)

        accessory_cats = {item.category_name for item in res.recommendations}
        self.assertIn("mouse", accessory_cats)
        self.assertIn("headphone", accessory_cats)


if __name__ == "__main__":
    unittest.main()
