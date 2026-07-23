-- AI Service Database Schema Draft
-- Product snapshot is copied from catalog-service.
-- Cross-service IDs are intentionally not real DB foreign keys.

CREATE TABLE IF NOT EXISTS ai_products (
  product_id BIGINT PRIMARY KEY,
  title TEXT NOT NULL,
  category_id BIGINT,
  category_name TEXT,
  brand TEXT,
  original_price INTEGER,
  discounted_price INTEGER,
  discount_percent INTEGER,
  average_rating DOUBLE PRECISION NOT NULL DEFAULT 0,
  num_ratings INTEGER NOT NULL DEFAULT 0,
  quantity_sold BIGINT,
  seller_id BIGINT,
  description TEXT,
  detailed_review TEXT,
  powerful_performance TEXT,
  battery_capacity TEXT,
  battery_type TEXT,
  color TEXT,
  connection_port TEXT,
  dimension TEXT,
  ram_capacity TEXT,
  rom_capacity TEXT,
  screen_size TEXT,
  weight TEXT,
  specs JSONB,
  tags TEXT[],
  image_url TEXT,
  content_text TEXT,
  source TEXT DEFAULT 'catalog-service',
  is_active BOOLEAN DEFAULT TRUE,
  last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_products_category_id
ON ai_products (category_id);

CREATE INDEX IF NOT EXISTS idx_ai_products_category_name
ON ai_products (category_name);

CREATE INDEX IF NOT EXISTS idx_ai_products_brand
ON ai_products (brand);

CREATE INDEX IF NOT EXISTS idx_ai_products_is_active
ON ai_products (is_active);

-- Future: enable pgvector before using VECTOR type.
-- CREATE EXTENSION IF NOT EXISTS vector;

-- Future table for vector embeddings.
-- CREATE TABLE IF NOT EXISTS product_embeddings (
--   product_id BIGINT PRIMARY KEY REFERENCES ai_products(product_id) ON DELETE CASCADE,
--   embedding VECTOR(1536),
--   model TEXT,
--   content_text TEXT,
--   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
-- );

-- Future table for chatbot RAG documents.
-- CREATE TABLE IF NOT EXISTS chat_documents (
--   id TEXT PRIMARY KEY,
--   title TEXT NOT NULL,
--   content TEXT NOT NULL,
--   document_type TEXT,
--   metadata JSONB,
--   embedding VECTOR(1536),
--   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
-- );
