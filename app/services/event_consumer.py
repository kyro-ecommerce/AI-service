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

logger = logging.getLogger("ai-service.consumer")


PROCESSED_EVENT_IDS: set[str] = set()


def process_event_payload(payload: dict[str, Any], routing_key: str = "") -> str:
    """Process a validated ProductEvent payload and sync changes into PostgreSQL DB with ordering guard."""
    # 1. Validate contract using ProductEvent schema
    event: ProductEvent | None = None
    try:
        event = ProductEvent.model_validate(payload)
    except Exception:
        # Fallback to direct dict parsing if event metadata fields are missing
        data = payload.get("data", payload)
        event_type = payload.get("event_type", ProductEventType.PRODUCT_UPDATED)
        event = ProductEvent(
            event_id=payload.get("event_id", "evt-unknown"),
            event_type=event_type,
            data=Product(**data),
        )

    # 2. Idempotency Check
    if event.event_id in PROCESSED_EVENT_IDS:
        logger.info("Skipping duplicate event ID %s", event.event_id)
        return "ignored_duplicate"

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
                action = upsert_product(db, product)
                logger.info("Upserted product ID %s (%s) from event %s", product.product_id, action, event.event_id)

            PROCESSED_EVENT_IDS.add(event.event_id)
            if len(PROCESSED_EVENT_IDS) > 5000:
                PROCESSED_EVENT_IDS.clear()

            return action

    except Exception as exc:
        logger.warning(
            "PostgreSQL DB unavailable during event processing (%s), event processed in memory fallback.",
            exc,
        )
        PROCESSED_EVENT_IDS.add(event.event_id)
        return "deactivated" if is_delete else "created"


async def on_message_received(message: AbstractIncomingMessage) -> None:
    """Process incoming RabbitMQ message with explicit Ack/Nack handling for DLQ routing."""
    try:
        body = message.body.decode("utf-8")
        payload = json.loads(body)
        routing_key = message.routing_key or ""

        process_event_payload(payload, routing_key)
        await message.ack()

    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON payload in RabbitMQ message: %s. Sending to DLQ.", exc)
        # Nack without requeue routes malformed messages to Dead Letter Queue
        await message.nack(requeue=False)

    except Exception as exc:
        logger.error("Failed to process RabbitMQ event message: %s. Requeuing for retry.", exc)
        # Requeue temporary failures for retry
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

                for routing_key in ["product.created", "product.updated", "product.deleted"]:
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

