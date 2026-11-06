"""Similar ranking with a fixed target SKU; no inference across SKU price pairs."""

from app.schemas.product import Product
from app.schemas.recommendation import (
    RecommendationItem, RecommendationResponse, RecommendationVariantEvidence,
)
from app.services.recommendation.ranker import rerank_similar_candidates, score_similar_candidate
from app.services.recommendation.retrieval import retrieve_candidates_for_similar
from app.services.recommendation.sku_projection import (
    SimilarSkuProjection, project_similar_skus, select_target_representative,
)

SIMILAR_SKU_POLICY_VERSION = "similar-sku-v1-catalog-name-claims"


def _evidence(row: SimilarSkuProjection) -> RecommendationVariantEvidence:
    return RecommendationVariantEvidence(
        variant_id=row.variant.variant_id, sku=row.variant.sku,
        variant_name=row.variant.variant_name, price_vnd=row.variant.price_vnd,
        sale_price_vnd=row.variant.sale_price_vnd, stock=row.variant.stock,
        ram_gb=row.capacities.ram_gb, storage_gb=row.capacities.storage_gb,
        ram_source=row.capacities.ram_source, storage_source=row.capacities.storage_source,
        parser_version=row.capacities.parser_version,
    )


def rank_similar_snapshot(
    products: list[Product], target: Product, limit: int = 5,
) -> RecommendationResponse:
    candidates = retrieve_candidates_for_similar(products, target, limit=None)
    target_result = project_similar_skus(target)
    candidate_results = [(p, project_similar_skus(p)) for p in candidates]
    if target_result.status == "absent_snapshot":
        # Flat comparison only when BOTH products lack a SKU snapshot. Do not
        # conceal empty/malformed snapshots or mix SKU and parent price bases.
        response = rerank_similar_candidates(target, [
            p for p, result in candidate_results if result.status == "absent_snapshot"
        ], limit)
        response.comparison_basis = "product_snapshot"
        response.comparison_status = "absent_snapshot"
        for item in response.recommendations:
            item.comparison_basis = "product_snapshot"
        return response

    representative = select_target_representative(target_result)
    ranked = []
    if representative is not None:
        for _, result in candidate_results:
            if result.status != "available":
                continue
            scored = [
                (*score_similar_candidate(representative.scoped_product, row.scoped_product), row)
                for row in result.projections
            ]
            score, reason, chosen = min(scored, key=lambda item: (
                -item[0], item[2].variant.sale_price_vnd, item[2].variant.variant_id,
            ))
            ranked.append((score, chosen, reason))
    ranked.sort(key=lambda item: (-item[0], item[1].scoped_product.product_id))
    items = []
    for score, chosen, reason in ranked[:limit]:
        p = chosen.scoped_product
        evidence = _evidence(chosen)
        # Existing FE ignores structured evidence: retain the SKU identity and
        # caution in reason too, without promising preselection or checkout.
        reason += (
            f"; SKU {evidence.sku} (variant {evidence.variant_id}); "
            "giá/cấu hình theo snapshot SKU, chưa xác minh độc lập; "
            "đối chiếu SKU đại diện, không phải SKU đang chọn; cần chọn đúng SKU khi mua"
        )
        items.append(RecommendationItem(
            product_id=p.product_id, title=p.title, category_id=p.category_id,
            category_name=p.category_name, brand=p.brand,
            original_price=p.original_price, discounted_price=p.discounted_price,
            discount_percent=p.discount_percent, average_rating=p.average_rating,
            image_url=p.image_url, similarity_score=round(score, 2), reason=reason,
            comparison_basis="sku_snapshot", selected_variant=evidence,
        ))
    return RecommendationResponse(
        target_product_id=target.product_id, target_product_title=target.title,
        strategy=SIMILAR_SKU_POLICY_VERSION, recommendations=items,
        comparison_basis="sku_snapshot", comparison_status=target_result.status,
        target_variant=_evidence(representative) if representative is not None else None,
        target_selection_policy="cheapest_active_in_stock_positive_sale",
    )
