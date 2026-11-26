"""Atomic projections for trusted Order/Catalog events (PostgreSQL row locks).

Stock versions belong to variants. Buffered deltas survive a snapshot arriving late.
"""
import logging
from datetime import datetime
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import AIProduct, IntegrationProjection, ProcessedEvent, UserInteraction
from app.schemas.order_event import OrderCreatedEvent, OrderCancelledEvent
from app.repositories.product_repository import deactivate_product, upsert_product
from app.repositories.user_interaction_repository import record_user_interaction
from app.services.backend_sync import map_dto_to_product
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY


logger = logging.getLogger(__name__)

def merge_stock(rows, known):
    """Keep lifecycle fields from snapshot, stock from the newest variant revision."""
    merged = []
    for row in rows:
        row = dict(row)
        key = str(row["variant_id"])
        previous = known.get(key)
        incoming = int(row.get("stock_version", 0))
        if previous and int(previous["stockVersion"]) > incoming:
            row.update(stock=previous["stock"], stock_version=previous["stockVersion"])
        else:
            known[key] = {"stock": row["stock"], "stockVersion": incoming}
        merged.append(row)
    return merged


def apply_event(db, payload, routing_key):
    event_id = payload.get("eventId") or payload.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Durable eventId required")
    version = payload.get("version", 0)
    if type(version) is not int or version < 0:
        raise ValueError("Nonnegative event version required")
    is_order = routing_key.startswith("order.")
    snapshot = payload.get("snapshot")
    aggregate_id = payload.get("orderId") if is_order else (
        snapshot.get("id") if isinstance(snapshot, dict) else
        payload.get("productId") or (payload.get("data") or {}).get("product_id")
    )
    if type(aggregate_id) is not int or aggregate_id <= 0:
        raise ValueError("Positive aggregate id required")
    key = f"{'order' if is_order else 'product'}:{aggregate_id}"
    db.execute(insert(IntegrationProjection).values(aggregate_key=key, version=0, state={})
               .on_conflict_do_nothing(index_elements=["aggregate_key"]))
    projection = db.scalar(select(IntegrationProjection).where(
        IntegrationProjection.aggregate_key == key).with_for_update())
    if db.get(ProcessedEvent, event_id):
        return "ignored_duplicate"
    state = dict(projection.state or {})
    action = "ignored_stale"
    if is_order:
        event = (OrderCancelledEvent if routing_key == "order.cancelled" else OrderCreatedEvent).model_validate(payload)
        if version > projection.version or not state:
            terminal = state.get("status") in {"order.cancelled", "order.delivered"}
            if state.get("status") == "order.delivered" and routing_key == "order.cancelled":
                # Consume the contradictory event/version without undoing a valid delivery.
                logger.warning("Ignored cancellation after delivery: order=%s event=%s version=%s",
                               aggregate_id, event_id, version)
                action = "ignored_conflict"
            elif not terminal or routing_key != "order.created":
                if routing_key == "order.cancelled":
                    db.execute(delete(UserInteraction).where(
                        UserInteraction.interaction_type == "PURCHASE",
                        UserInteraction.event_id.startswith(f"order-delivered:{aggregate_id}:")))
                    # Remove legacy order.created purchases as well.
                    db.execute(delete(UserInteraction).where(
                        UserInteraction.interaction_type == "PURCHASE",
                        UserInteraction.event_id.startswith(f"order-created:{aggregate_id}:")))
                    action = "order_purchase_retracted"
                elif routing_key == "order.delivered":
                    occurred = payload.get("occurredAt")
                    if not occurred:
                        raise ValueError("Delivery occurredAt required")
                    occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    if occurred.tzinfo is None:
                        raise ValueError("Delivery occurredAt must have timezone")
                    for item in event.items:
                        record_user_interaction(db, event.userId, "PURCHASE",
                            f"Order {aggregate_id} delivered", product_id=item.productId,
                            event_id=f"order-delivered:{aggregate_id}:{item.productId}:{item.variantId or 0}",
                            occurred_at=occurred, occurred_at_source="event_provided",
                            context_data={"order_id": aggregate_id, "quantity": item.quantity,
                                          "source": "order_service_event"}, commit=False, raise_on_error=True)
                    action = "order_purchase_recorded"
                else:
                    action = "order_created_recorded"
                state["status"] = routing_key
            projection.version = version
    else:
        known = dict(state.get("stock", {}))
        if routing_key == "stock.changed":
            variant_id, stock, stock_version = payload.get("variantId"), payload.get("stock"), payload.get("stockVersion")
            if any(type(v) is not int for v in (variant_id, stock, stock_version)) or variant_id <= 0 or stock < 0 or stock_version < 0:
                raise ValueError("Invalid stock delta")
            prior = known.get(str(variant_id))
            if not prior or stock_version > prior["stockVersion"]:
                known[str(variant_id)] = {"stock": stock, "stockVersion": stock_version}
                current = db.get(AIProduct, aggregate_id)
                if current is not None and not state.get("deleted"):
                    specs = dict(current.specs or {})
                    specs[VARIANT_SNAPSHOT_KEY] = merge_stock(specs.get(VARIANT_SNAPSHOT_KEY, []), known)
                    current.specs = specs
                action = "stock_updated"
        elif version > projection.version or not state.get("lifecycle_seen"):
            if routing_key == "product.deleted":
                deactivate_product(db, aggregate_id, commit=False, source="catalog-event")
                state["deleted"] = True
                action = "deactivated"
            else:
                if not isinstance(snapshot, dict):
                    raise ValueError("Versioned lifecycle event requires full snapshot")
                product = map_dto_to_product(snapshot)
                specs = dict(product.specs or {})
                specs[VARIANT_SNAPSHOT_KEY] = merge_stock(specs.get(VARIANT_SNAPSHOT_KEY, []), known)
                product = product.model_copy(update={"specs": specs})
                action = upsert_product(db, product, commit=False, source="catalog-event")
                state["deleted"] = False
            state["lifecycle_seen"] = True
            projection.version = version
        state["stock"] = known
    projection.state = state
    db.add(ProcessedEvent(event_id=event_id, event_type=routing_key, routing_key=routing_key))
    # Caller commits projection, business writes and inbox together.
    return action
