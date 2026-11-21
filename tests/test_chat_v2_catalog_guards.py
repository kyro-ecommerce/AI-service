"""Catalog guarantees carried over from the retired single-turn pipeline,
now asserted against chat v2: SKU grouping and pricing, missing data never
qualifying, store QA bounds, catalog failures, JSON/SSE parity."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.product_repository import CatalogUnavailableError
from app.schemas.product import Product
from app.services import chat_common, chat_v2
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY
from tests.conftest import FAKE_CONVERSATIONS

SKU_QUERY = "Laptop dưới 30 triệu RAM tối thiểu 16 GB"


def sku_product():
    from app.services.backend_sync import map_dto_to_product

    variants = [
        (201, "FIXTURE-8GB", "8GB/512GB", 19_000_000, 19_000_000),
        (202, "FIXTURE-16GB", "16GB/1TB", 28_000_000, 25_000_000),
        (203, "FIXTURE-32GB", "32GB/2TB", 27_000_000, 26_000_000),
    ]
    return map_dto_to_product({
        "id": 101, "title": "Fixture laptop with SKUs", "topLevelCategory": "Laptop",
        "minPrice": 19_000_000, "minSalePrice": 19_000_000,
        "variants": [
            {"id": vid, "sku": sku, "variantName": name, "price": price, "salePrice": sale,
             "stock": 1, "active": True}
            for vid, sku, name, price, sale in variants
        ],
    })


def plain(pid, *, ram="16 GB", price=18_000_000, active=True):
    return Product(product_id=pid, title=f"Laptop fixture {pid}", category_name="laptop",
                   ram_capacity=ram, original_price=price, is_active=active)


@pytest.fixture
def catalog(monkeypatch):
    FAKE_CONVERSATIONS.clear()
    products: list = []
    monkeypatch.setattr(chat_common, "_load_chat_catalog_snapshot", lambda: (products, "database"))
    yield products
    FAKE_CONVERSATIONS.clear()


def chat(message, limit=4):
    return asyncio.run(chat_v2.process_turn(
        message, conversation_id=None, user_id=0, session_id="s-guard", limit=limit,
    ))


def ids(response):
    return [p.product_id for p in response.recommended_products]


# ------------------------------------------------------------ SKU snapshots
def test_one_card_per_product_priced_from_the_cheapest_matching_sku(catalog):
    original = sku_product()
    catalog.append(original)
    response = chat(SKU_QUERY)
    assert ids(response) == [101]
    card = response.recommended_products[0]
    assert [v.variant_id for v in card.matched_variants] == [202, 203]
    assert (card.original_price, card.discounted_price, card.minSalePrice) == (28_000_000, 25_000_000, 25_000_000)
    assert original.original_price == 19_000_000          # snapshot not mutated
    assert all(v.verified is False for v in card.matched_variants)


def test_rejected_sku_price_never_qualifies_the_parent(catalog):
    catalog.append(sku_product())
    assert ids(chat("Laptop dưới 20 triệu RAM tối thiểu 16 GB")) == []


def test_sku_without_parsed_ram_cannot_inherit_parent_ram(catalog):
    product = sku_product()
    product.ram_capacity = "128 GB"
    for row in product.specs[VARIANT_SNAPSHOT_KEY]:
        row.pop("name_proposal")
    catalog.append(product)
    assert ids(chat(SKU_QUERY)) == []


def test_inactive_product_cannot_qualify_through_active_skus(catalog):
    product = sku_product()
    product.is_active = False
    catalog.append(product)
    assert ids(chat(SKU_QUERY)) == []


def test_sku_with_unknown_price_cannot_pass_budget(catalog):
    product = sku_product()
    for row in product.specs[VARIANT_SNAPSHOT_KEY]:
        row.update(price_vnd=0, sale_price_vnd=0)
    catalog.append(product)
    assert ids(chat(SKU_QUERY)) == []


def test_limit_counts_products_not_skus(catalog):
    catalog.extend([sku_product(), plain(1)])
    response = chat(SKU_QUERY, limit=1)
    assert len(response.recommended_products) == 1


@pytest.mark.parametrize(
    "original,discounted,expected",
    [(18_000_000, None, 18_000_000), (18_000_000, 0, 18_000_000),
     (18_000_000, 16_000_000, 16_000_000), (None, None, None), (0, 0, None)],
)
def test_frontend_price_alias_is_computed_not_trusted_input(original, discounted, expected):
    from app.schemas.chat import RecommendedProductSummary

    card = RecommendedProductSummary(product_id=1, title="Fixture", original_price=original,
                                     discounted_price=discounted, minSalePrice=1)
    assert card.model_dump()["minSalePrice"] == expected


# ------------------------------------------------------------ missing data
def test_unknown_ram_is_not_inferred_from_title(catalog):
    product = plain(4, ram=None)
    product.title = "Laptop RAM 16GB 32GB"
    catalog.append(product)
    assert ids(chat("laptop RAM 16GB dưới 20 triệu")) == []


def test_empty_catalog_gives_no_cards_not_fake_products(catalog):
    response = chat("laptop dưới 20 triệu")
    assert ids(response) == [] and response.reply


def test_failed_catalog_is_an_error_not_an_empty_answer(monkeypatch):
    def broken():
        raise CatalogUnavailableError("fixture unavailable")

    monkeypatch.setattr(chat_common, "_load_chat_catalog_snapshot", broken)
    with pytest.raises(CatalogUnavailableError):
        chat("laptop dưới 20 triệu")


# ------------------------------------------------------------ store QA
@pytest.mark.parametrize("query,topic", [
    ("chính sách bảo hành như thế nào?", "bảo hành"),
    ("chính sách bảo hành thế nào", "bảo hành"),
    ("giao hàng mấy ngày?", "giao hàng"),
    ("Shop ơi đổi trả như thế nào?", "đổi trả"),
    ("địa chỉ của cửa hàng ở đâu?", "địa chỉ"),
])
def test_store_policy_answers_admit_there_is_no_verified_source(catalog, query, topic):
    response = chat(query)
    assert ids(response) == []
    assert topic in response.reply and "chưa có nguồn" in response.reply
    assert not any(claim in response.reply for claim in ("12 - 24", "30 ngày", "2h"))


def test_assistant_identity_does_not_claim_authenticity(catalog):
    reply = chat_common.get_store_qa_response("ban la ai")
    assert "trợ lý AI" in reply and "chính hãng" not in reply


# ------------------------------------------------------------ JSON / SSE parity
def test_json_and_sse_share_cards_and_reply(catalog):
    catalog.append(sku_product())
    client = TestClient(app)
    try:
        payload = {"message": SKU_QUERY, "user_id": 0, "limit": 6}
        normal = client.post("/api/v1/ai/chat", json=payload)
        stream = client.post("/api/v1/ai/chat/stream", json=payload)
        assert normal.status_code == stream.status_code == 200
        assert set(normal.json()) == {
            "reply", "recommended_products", "source", "recommendation_id", "model_version",
            "conversation_id", "conditions", "intent",
        }
        frames = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        assert frames[0]["recommended_products"] == normal.json()["recommended_products"]
        assert "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.json()["reply"]
        assert frames[-1] == {"type": "done"}
    finally:
        client.close()
