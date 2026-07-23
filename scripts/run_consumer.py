import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.event_consumer import start_event_consumer

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    print("Starting AI Service RabbitMQ Consumer...")
    await start_event_consumer()


if __name__ == "__main__":
    asyncio.run(main())
