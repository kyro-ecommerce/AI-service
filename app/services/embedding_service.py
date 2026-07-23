import logging
from typing import Any

logger = logging.getLogger("ai-service.embedding")

_model: Any = None
MODEL_NAME = "all-MiniLM-L6-v2"
VECTOR_DIMENSION = 384


def get_embedding_model() -> Any:
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformer model: %s", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME)
        except Exception as exc:
            logger.error("Failed to load SentenceTransformer model %s: %s", MODEL_NAME, exc)
            raise exc

    return _model


def generate_embedding(text: str) -> list[float]:
    """Generate a 384-dimensional dense vector embedding for the given text."""
    if not text or not text.strip():
        return [0.0] * VECTOR_DIMENSION

    model = get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)

    return embedding.tolist()
