from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Text, func
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
    original_price: Mapped[int | None] = mapped_column(Integer)
    discounted_price: Mapped[int | None] = mapped_column(Integer)
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
    source: Mapped[str | None] = mapped_column(Text, default="catalog-service")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    interaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    category_intents: Mapped[dict | list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

