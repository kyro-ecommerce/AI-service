import unittest

from app.db.models import AIProduct
from app.repositories.product_repository import apply_product_fields
from app.schemas.product import Product
from app.services.product_content import build_specs
from app.services.search_service import search_products


class ProductSpecsSourceOfTruthTest(unittest.TestCase):
    def test_flat_spec_fields_override_conflicting_specs_json(self) -> None:
        product = Product(
            product_id=1,
            title="Lenovo IdeaPad Slim 5",
            ram_capacity="16GB",
            specs={
                "ram_capacity": "8GB",
                "material": "aluminum",
            },
        )

        specs = build_specs(product)

        self.assertEqual(specs["ram_capacity"], "16GB")
        self.assertEqual(specs["material"], "aluminum")

    def test_apply_product_fields_stores_flat_specs_and_derived_specs(self) -> None:
        product = Product(
            product_id=1,
            title="Lenovo IdeaPad Slim 5",
            category_name="laptop",
            brand="Lenovo",
            description="Laptop mong nhe cho sinh vien",
            ram_capacity="16GB",
            rom_capacity="512GB",
            screen_size="14 inch",
            specs={"ram_capacity": "8GB"},
        )
        db_product = AIProduct(product_id=product.product_id)

        apply_product_fields(db_product, product)

        self.assertEqual(db_product.ram_capacity, "16GB")
        self.assertEqual(db_product.rom_capacity, "512GB")
        self.assertEqual(db_product.screen_size, "14 inch")
        self.assertEqual(db_product.specs["ram_capacity"], "16GB")
        self.assertEqual(db_product.specs["rom_capacity"], "512GB")
        self.assertEqual(db_product.specs["screen_size"], "14 inch")
        self.assertIn("16GB", db_product.content_text)
        self.assertIn("512GB", db_product.content_text)
        self.assertIn("14 inch", db_product.content_text)

    def test_search_keeps_matching_electronics_specs_from_tags_and_specs(self) -> None:
        product = Product(
            product_id=1,
            title="Lenovo IdeaPad Slim 5",
            category_name="laptop",
            brand="Lenovo",
            ram_capacity="16GB",
            rom_capacity="512GB",
            screen_size="14 inch",
            average_rating=4.7,
        )

        results = search_products([product], "16GB 512GB 14 inch", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].product_id, product.product_id)
        self.assertGreater(results[0].score, 0)


if __name__ == "__main__":
    unittest.main()
