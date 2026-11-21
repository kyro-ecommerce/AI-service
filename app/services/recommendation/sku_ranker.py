"""Similar ranking with a fixed target SKU; no inference across SKU price pairs."""

from app.schemas.product import Product
from app.schemas.recommendation import (
    RecommendationItem, RecommendationResponse, RecommendationVariantEvidence,
)
from app.services.recommendation.ranker import rerank_similar_candidates, score_similar_candidate
from app.services.recommendation.retrieval import retrieve_candidates_for_similar
from app.services.recommendation.sku_projection import (
    SimilarSkuProjection, project_similar_skus, select_target_variant, capacity_proximity,
)

SIMILAR_SKU_POLICY_VERSION = "similar-sku-v2.1-explicit-target"
# Same ranking with embedding cosine as the content signal (GĐ3); a separate
# name lets online metrics compare it with the keyword-overlap fallback.
SIMILAR_SKU_VECTOR_POLICY_VERSION = "similar-sku-v2.2-vector"


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
    target_variant_id: int | None = None,
    ranking_policy: str = "hybrid",
) -> RecommendationResponse:
    if ranking_policy not in {"hybrid", "capacity_first"}:
        raise ValueError("Unsupported similar ranking policy")
    candidates = retrieve_candidates_for_similar(products, target, limit=None)
    target_result = project_similar_skus(target)
    representative = select_target_variant(target_result, target_variant_id)
    if ranking_policy == "capacity_first" and (
        representative is None or capacity_proximity(representative, representative) is None
    ):
        return RecommendationResponse(
            target_product_id=target.product_id, target_product_title=target.title,
            strategy="capacity-first-v1-experimental", ranking_policy=ranking_policy,
            comparison_basis="sku_snapshot", comparison_status="unknown_target_capacities",
            target_variant=_evidence(representative) if representative else None,
            target_selection_policy="requested_variant" if target_variant_id is not None else "cheapest_active_in_stock_positive_sale",
        )
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

    ranked = []
    if representative is not None:
        for _, result in candidate_results:
            if result.status != "available":
                continue
            scored = [
                (*score_similar_candidate(representative.scoped_product, row.scoped_product), row)
                for row in result.projections
            ]
            def priority(item):
                score, _, row = item
                if ranking_policy == "capacity_first":
                    return (-(capacity_proximity(representative, row) or 0), row.variant.sale_price_vnd, -score, row.variant.variant_id)
                return (-score, row.variant.sale_price_vnd, row.variant.variant_id)
            score, reason, chosen = min(scored, key=priority)
            ranked.append((score, chosen, reason))
    if ranking_policy == "capacity_first":
        ranked.sort(key=lambda item: (
            -(capacity_proximity(representative, item[1]) or 0), item[1].variant.sale_price_vnd,
            -item[0], item[1].scoped_product.product_id, item[1].variant.variant_id,
        ))
    else:
        ranked.sort(key=lambda item: (-item[0], item[1].scoped_product.product_id))
    items = []
    for score, chosen, reason in ranked[:limit]:
        p = chosen.scoped_product
        evidence = _evidence(chosen)
        proximity = capacity_proximity(representative, chosen)
        if ranking_policy == "capacity_first":
            reason = "Thử nghiệm: ưu tiên độ gần RAM/lưu trữ trong catalog, bằng nhau ưu tiên giá thấp hơn; không đánh giá hiệu năng CPU/GPU"
        # Keep plain-text consumers honest about the actual target selection.
        target_note = (
            f"đối chiếu SKU gốc được chọn {representative.variant.sku} (variant {target_variant_id})"
            if target_variant_id is not None else "đối chiếu SKU đại diện, không phải SKU đang chọn"
        )
        reason += (
            f"; SKU {evidence.sku} (variant {evidence.variant_id}); "
            "giá/cấu hình theo snapshot SKU, chưa xác minh độc lập; "
            f"{target_note}; cần chọn đúng SKU khi mua"
        )
        items.append(RecommendationItem(
            product_id=p.product_id, title=p.title, category_id=p.category_id,
            category_name=p.category_name, brand=p.brand,
            original_price=p.original_price, discounted_price=p.discounted_price,
            discount_percent=p.discount_percent, average_rating=p.average_rating,
            image_url=p.image_url, similarity_score=round(score, 2), reason=reason,
            comparison_basis="sku_snapshot", selected_variant=evidence,
            capacity_proximity=proximity,
        ))
    return RecommendationResponse(
        target_product_id=target.product_id, target_product_title=target.title,
        strategy=(
            "capacity-first-v1-experimental" if ranking_policy == "capacity_first"
            else SIMILAR_SKU_VECTOR_POLICY_VERSION if target.embedding
            else SIMILAR_SKU_POLICY_VERSION
        ),
        ranking_policy=ranking_policy, recommendations=items,
        comparison_basis="sku_snapshot", comparison_status=target_result.status,
        target_variant=_evidence(representative) if representative is not None else None,
        target_selection_policy="requested_variant" if target_variant_id is not None else "cheapest_active_in_stock_positive_sale",
    )
