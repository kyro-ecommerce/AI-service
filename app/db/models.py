from datetime import datetime

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    from sqlalchemy import String as Vector

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIProduct(Base):
    __tablename__ = "ai_products"

    product_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    category_name: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    original_price: Mapped[int | None] = mapped_column(BigInteger)
    discounted_price: Mapped[int | None] = mapped_column(BigInteger)
    discount_percent: Mapped[int | None] = mapped_column(Integer)
    average_rating: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    num_ratings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity_sold: Mapped[int | None] = mapped_column(BigInteger)
    seller_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    detailed_review: Mapped[str | None] = mapped_column(Text)
    powerful_performance: Mapped[str | None] = mapped_column(Text)
    battery_capacity: Mapped[str | None] = mapped_column(Text)
    battery_type: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    connection_port: Mapped[str | None] = mapped_column(Text)
    dimension: Mapped[str | None] = mapped_column(Text)
    ram_capacity: Mapped[str | None] = mapped_column(Text)
    rom_capacity: Mapped[str | None] = mapped_column(Text)
    screen_size: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[str | None] = mapped_column(Text)
    specs: Mapped[dict | None] = mapped_column(JSONB)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    image_url: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    source: Mapped[str | None] = mapped_column(Text, default="unknown", server_default="unknown")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    available_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserInteraction(Base):
    __tablename__ = "user_interactions"
    __table_args__ = (
        CheckConstraint(
            "occurred_at_source IN ('event_provided', 'server_recorded', 'legacy_unknown')",
            name="ck_user_interactions_occurred_at_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    interaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    category_intents: Mapped[dict | list | None] = mapped_column(JSONB)
    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    session_id: Mapped[str | None] = mapped_column(Text, index=True)
    event_id: Mapped[str | None] = mapped_column(Text, unique=True)
    request_id: Mapped[str | None] = mapped_column(Text, index=True)
    recommendation_id: Mapped[str | None] = mapped_column(Text, index=True)
    position: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    context_data: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="legacy_unknown", server_default="legacy_unknown",
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ProcessedEvent(Base):
    """Durable idempotency record for successfully committed broker events."""

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    routing_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CatalogReconciliationRun(Base):
    __tablename__ = "catalog_reconciliation_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    before_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    backend_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    backup_path: Mapped[str] = mapped_column(Text, nullable=False)
    backup_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    backend_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AIProductLegacyArchive(Base):
    __tablename__ = "ai_product_legacy_archive"

    run_id: Mapped[str] = mapped_column(Text, ForeignKey("catalog_reconciliation_runs.run_id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

