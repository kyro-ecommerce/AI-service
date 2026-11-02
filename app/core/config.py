import os
from pathlib import Path
from dotenv import load_dotenv
from app.core.internal_credentials import resolve_internal_token

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/ai_service",
)

RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)

RABBITMQ_EXCHANGE = os.getenv(
    "RABBITMQ_EXCHANGE",
    "product.events",
)

RABBITMQ_QUEUE = os.getenv(
    "RABBITMQ_QUEUE",
    "ai.product.events",
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

LLM_REQUEST_TIMEOUT_SECONDS = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "8"))
LLM_TOTAL_BUDGET_SECONDS = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "12"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "768"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# A real, independently verified HF commit is required before embedding use.
# Empty revision intentionally disables encoding; never fall back to main/latest.
EMBEDDING_MODEL_NAME = os.getenv(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_MODEL_REVISION = os.getenv("EMBEDDING_MODEL_REVISION", "")

CHAT_RATE_LIMIT_REQUESTS = int(os.getenv("CHAT_RATE_LIMIT_REQUESTS", "20"))
CHAT_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("CHAT_RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_TRUST_PROXY_HEADERS = os.getenv(
    "RATE_LIMIT_TRUST_PROXY_HEADERS", "false"
).lower() in {"1", "true", "yes"}

CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "http://localhost:8080")

# Shared only between trusted backend services. User-facing clients must never
# receive this value.
INTERNAL_API_TOKEN = resolve_internal_token(os.environ, Path(__file__).resolve().parents[2])





