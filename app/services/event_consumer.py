import asyncio
import json
import logging
from collections import deque
from typing import Any
from urllib.parse import urlsplit

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import RABBITMQ_EXCHANGE, RABBITMQ_QUEUE, RABBITMQ_URL
from app.core.product_source import ProductSourceConflictError
from app.db.models import ProcessedEvent
from app.db.session import SessionLocal
from app.repositories.product_repository import deactivate_product, upsert_product
from app.repositories.user_interaction_repository import record_user_interaction
from app.schemas.product import Product
from app.schemas.product_event import ProductEvent, ProductEventType
from app.schemas.user_event import UserProductEvent
from app.services.backend_sync import fetch_product_dto_from_backend, map_dto_to_product
from app.services.recommendation.caching import recommendation_response_cache

logger = logging.getLogger("ai-service.consumer")


class InvalidEventPayload(ValueError):
    """A permanent contract error that must not be retried indefinitely."""


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
    """Apply an event and its idempotency marker in one database transaction."""
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise InvalidEventPayload("event_id must be a non-empty string")
    event_id = event_id.strip()

    # Fast path for duplicates seen by this process. PostgreSQL remains the
    # source of truth across restarts and multiple consumer replicas.
    if PROCESSED_EVENTS.contains(event_id):
        logger.info("Skipping duplicate event ID %s", event_id)
        return "ignored_duplicate"

    event_type_str = str(payload.get("event_type", ""))
    is_clickstream = routing_key.startswith("user.") or event_type_str.startswith("User")
    click_event: UserProductEvent | None = None
    if is_clickstream:
        try:
            click_event = UserProductEvent.model_validate(payload)
        except ValidationError as exc:
            raise InvalidEventPayload(str(exc)) from exc
    action = ""

    try:
        with SessionLocal() as db:
            existing = db.get(ProcessedEvent, event_id)
            if existing is not None:
                PROCESSED_EVENTS.add(event_id)
                logger.info("Skipping durable duplicate event ID %s", event_id)
                return "ignored_duplicate"

            if is_clickstream:
                if click_event is None:
                    raise InvalidEventPayload("clickstream event validation failed")
                user_id = click_event.user_id
                cat_name = click_event.category_name or click_event.category or ""
                event_hint = f"{routing_key} {event_type_str}".lower()
                if any(keyword in event_hint for keyword in ["order", "purchase", "completed"]):
                    action_type = "PURCHASE"
                elif "cart" in event_hint:
                    action_type = "ADD_TO_CART"
                elif "impression" in event_hint:
                    action_type = "IMPRESSION"
                elif "click" in event_hint:
                    action_type = "CLICK"
                else:
                    action_type = "VIEW"

                record_user_interaction(
                    db=db,
                    user_id=user_id,
                    interaction_type=action_type,
                    query_text=(
                        f"Realtime {action_type} for {cat_name}"
                        if cat_name
                        else f"Realtime {action_type} for product {click_event.product_id}"
                    ),
                    category_intents=[cat_name] if cat_name else [],
                    commit=False,
                    raise_on_error=True,
                    product_id=click_event.product_id,
                    session_id=click_event.session_id,
                    event_id=event_id,
                    occurred_at=click_event.occurred_at,
                    context_data={"routing_key": routing_key},
                    request_id=click_event.request_id,
                    recommendation_id=click_event.recommendation_id,
                    position=click_event.position,
                    model_version=click_event.model_version,
                )
                logger.info(
                    "Processed clickstream event for user %s: action=%s, product=%s",
                    user_id,
                    action_type,
                    click_event.product_id,
                )
                action = "user_clickstream_recorded"
            else:
                try:
                    event = ProductEvent.model_validate(payload)
                except ValidationError:
                    try:
                        event = ProductEvent.model_validate(
                            {
                                "event_id": event_id,
                                "event_type": payload.get(
                                    "event_type", ProductEventType.PRODUCT_UPDATED
                                ),
                                "occurred_at": payload.get("occurred_at"),
                                "data": Product(**payload.get("data", payload)),
                            }
                        )
                    except (ValidationError, TypeError) as exc:
                        raise InvalidEventPayload(str(exc)) from exc

                product = event.data
                is_delete = (
                    event.event_type == ProductEventType.PRODUCT_DELETED
                    or routing_key == "product.deleted"
                )
                if is_delete:
                    deactivate_product(
                        db,
                        product.product_id,
                        commit=False,
                        deactivated_at=event.occurred_at,
                        source="catalog-event",
                    )
                    action = "deactivated"
                else:
                    if not product.description:
                        backend_dto = fetch_product_dto_from_backend(product.product_id)
                        if backend_dto:
                            product = map_dto_to_product(backend_dto, fallback_product=product)
                    action = upsert_product(
                        db,
                        product,
                        commit=False,
                        available_at=event.occurred_at,
                        source="catalog-event",
                    )

            db.add(
                ProcessedEvent(
                    event_id=event_id,
                    event_type=event_type_str or routing_key or "unknown",
                    routing_key=routing_key,
                )
            )
            db.commit()
    except ProductSourceConflictError as exc:
        # A conflicting namespace needs manual reconciliation, not infinite retry.
        raise InvalidEventPayload(str(exc)) from exc
    except IntegrityError:
        # Two consumers may race on the same event_id. If the other transaction
        # won, the unique key makes this delivery a safe duplicate.
        with SessionLocal() as verification_db:
            if verification_db.get(ProcessedEvent, event_id) is not None:
                PROCESSED_EVENTS.add(event_id)
                return "ignored_duplicate"
        raise

    PROCESSED_EVENTS.add(event_id)
    _invalidate_caches()
    return action


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
        if not isinstance(payload, dict):
            raise InvalidEventPayload("event payload must be a JSON object")
        routing_key = message.routing_key or ""

        await asyncio.to_thread(process_event_payload, payload, routing_key)
        await message.ack()

    except (json.JSONDecodeError, UnicodeDecodeError, InvalidEventPayload) as exc:
        logger.error("Invalid RabbitMQ event payload: %s. Sending to DLQ.", exc)
        await message.nack(requeue=False)

    except Exception as exc:
        logger.error("Failed to process RabbitMQ event message: %s. Requeuing for retry.", exc)
        await message.nack(requeue=True)


async def start_event_consumer_loop(retry_interval: int = 5) -> None:
    """Resilient background loop connecting to RabbitMQ with automatic retry on failure."""
    logger.info("Starting AI Service RabbitMQ Consumer background worker...")

    while True:
        try:
            broker = urlsplit(RABBITMQ_URL)
            logger.info("Attempting RabbitMQ connection to %s:%s...", broker.hostname, broker.port or 5672)
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

                for routing_key in ["product.created", "product.updated", "product.deleted", "user.product.impression", "user.product.clicked", "user.product.viewed", "user.cart.added", "user.order.completed"]:
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

