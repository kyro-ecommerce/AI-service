import os
from dotenv import load_dotenv

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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")




