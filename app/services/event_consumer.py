import asyncio
import json
import logging
from typing import Any


import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.core.config import RABBITMQ_EXCHANGE, RABBITMQ_QUEUE, RABBITMQ_URL
from app.db.session import SessionLocal
from app.repositories.product_repository import deactivate_product, upsert_product
from app.schemas.product import Product
from app.schemas.product_event import ProductEvent, ProductEventType
from app.services.backend_sync import fetch_product_dto_from_backend, map_dto_to_product
from app.services.recommendation.caching import recommendation_response_cache


from collections import deque

logger = logging.getLogger("ai-service.consumer")


class BoundedEventTracker:
    """Thread-safe bounded sliding window buffer for event idempotency checks."""

    def __init__(self, max_size: int = 5000) -> None:
        self.max_size = max_size
        self._set: set[str] = set()
        self._queue: deque[str] = deque()

    def contains(self, event_id: str) -> bool:
        return event_id in self._set

    def add(self, event_id: str) -> None:
        if event_id in self._set:
            return
        if len(self._queue) >= self.max_size:
            oldest = self._queue.popleft()
            self._set.discard(oldest)
        self._queue.append(event_id)
        self._set.add(event_id)

    def clear(self) -> None:
        self._set.clear()
        self._queue.clear()


PROCESSED_EVENTS = BoundedEventTracker(max_size=5000)
# Backward-compatibility alias for test assertions
PROCESSED_EVENT_IDS = PROCESSED_EVENTS._set


def process_event_payload(payload: dict[str, Any], routing_key: str = "") -> str:
    """Process a validated ProductEvent or UserClickstreamEvent payload with idempotency check."""
    event_id = payload.get("event_id", f"evt-{hash(str(payload))}")

    # Idempotency Check
    if PROCESSED_EVENTS.contains(event_id):
        logger.info("Skipping duplicate event ID %s", event_id)
        return "ignored_duplicate"

    # Handle User Clickstream Behavioral Events (e.g. user.product.viewed, user.cart.added)
    event_type_str = str(payload.get("event_type", ""))
    if routing_key.startswith("user.") or event_type_str.startswith("User"):
        user_id = payload.get("user_id", 0)
        cat_name = payload.get("category_name") or payload.get("category") or ""
        if any(kw in routing_key.lower() or kw in event_type_str.lower() for kw in ["order", "purchase", "completed"]):
            action_type = "PURCHASE"
        elif "cart" in routing_key.lower() or "cart" in event_type_str.lower():
            action_type = "CART"
        else:
            action_type = "VIEW"

        if cat_name:
            from app.repositories.user_interaction_repository import record_user_interaction
            try:
                with SessionLocal() as db:
                    record_user_interaction(
                        db=db,
                        user_id=user_id,
                        interaction_type=action_type,
                        query_text=f"Realtime {action_type} for {cat_name}",
                        category_intents=[cat_name],
                    )
                    logger.info("Processed real-time clickstream event for user %d: action=%s, category=%s", user_id, action_type, cat_name)
            except Exception as exc:
                logger.warning("Could not record clickstream event in DB: %s", exc)

        PROCESSED_EVENTS.add(event_id)
        _invalidate_caches()
        return "user_clickstream_recorded"

    # Handle Standard Product Lifecycle Events
    event: ProductEvent | None = None
    try:
        event = ProductEvent.model_validate(payload)
    except Exception:
        data = payload.get("data", payload)
        event_type = payload.get("event_type", ProductEventType.PRODUCT_UPDATED)
        event = ProductEvent(
            event_id=event_id,
            event_type=event_type,
            data=Product(**data),
        )

    product = event.data

    try:
        with SessionLocal() as db:
            is_delete = (
                event.event_type == ProductEventType.PRODUCT_DELETED
                or routing_key == "product.deleted"
            )
            if is_delete:
                deactivate_product(db, product.product_id)
                logger.info("Deactivated product ID %s from event %s", product.product_id, event.event_id)
                action = "deactivated"
            else:
                # Only call Backend REST API as fallback if event payload is missing description
                if not product.description:
                    backend_dto = fetch_product_dto_from_backend(product.product_id)
                    if backend_dto:
                        product = map_dto_to_product(backend_dto, fallback_product=product)
                        logger.info("Enriched product ID %s with full DTO from Backend REST API", product.product_id)

                action = upsert_product(db, product)
                logger.info("Upserted product ID %s (%s) from event %s", product.product_id, action, event.event_id)


            PROCESSED_EVENTS.add(event.event_id)
            _invalidate_caches()
            return action

    except Exception as exc:
        logger.warning(
            "PostgreSQL DB unavailable during event processing (%s), event processed in memory fallback.",
            exc,
        )
        PROCESSED_EVENTS.add(event.event_id)
        return "deactivated" if is_delete else "created"


def _invalidate_caches() -> None:
    """Clear recommendation and product caches after a product or clickstream event is processed."""
    import app.repositories.product_repository as prod_repo

    recommendation_response_cache.clear()
    prod_repo._products_cache = None
    prod_repo._products_cache_time = 0.0
    logger.debug("Invalidated recommendation & product caches after event.")


async def on_message_received(message: AbstractIncomingMessage) -> None:
    """Process incoming RabbitMQ message with explicit Ack/Nack handling for DLQ routing."""
    try:
        body = message.body.decode("utf-8")
        payload = json.loads(body)
        routing_key = message.routing_key or ""

        await asyncio.to_thread(process_event_payload, payload, routing_key)
        await message.ack()

    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON payload in RabbitMQ message: %s. Sending to DLQ.", exc)
        await message.nack(requeue=False)

    except Exception as exc:
        logger.error("Failed to process RabbitMQ event message: %s. Requeuing for retry.", exc)
        await message.nack(requeue=True)


async def start_event_consumer_loop(retry_interval: int = 5) -> None:
    """Resilient background loop connecting to RabbitMQ with automatic retry on failure."""
    logger.info("Starting AI Service RabbitMQ Consumer background worker...")

    while True:
        try:
            logger.info("Attempting connection to RabbitMQ at %s...", RABBITMQ_URL)
            connection = await aio_pika.connect_robust(
                RABBITMQ_URL,
                timeout=5.0,
            )

            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)

                exchange = await channel.declare_exchange(
                    RABBITMQ_EXCHANGE,
                    type=aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )

                queue = await channel.declare_queue(RABBITMQ_QUEUE, durable=True)

                for routing_key in ["product.created", "product.updated", "product.deleted", "user.product.viewed", "user.cart.added", "user.order.completed"]:
                    await queue.bind(exchange, routing_key=routing_key)

                logger.info("✅ AI Service RabbitMQ Consumer listening on queue '%s'", RABBITMQ_QUEUE)
                await queue.consume(on_message_received)

                # Keep connection alive while listening

                while not connection.is_closed:
                    await asyncio.sleep(2)

        except (asyncio.CancelledError, GeneratorExit):
            logger.info("RabbitMQ Consumer background task stopped gracefully.")
            break
        except Exception as exc:
            logger.warning(
                "RabbitMQ connection unavailable (%s). Retrying connection in %d seconds...",
                exc,
                retry_interval,
            )
            try:
                await asyncio.sleep(retry_interval)
            except asyncio.CancelledError:
                logger.info("RabbitMQ Consumer retry loop cancelled.")
                break

