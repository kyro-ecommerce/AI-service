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
            import os
            os.environ["USE_TF"] = "0"
            os.environ["USE_TORCH"] = "1"
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformer model: %s", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME, device="cpu")
        except Exception as exc:
            logger.error("Failed to load SentenceTransformer model %s: %s", MODEL_NAME, exc)
            return None

    return _model


from functools import lru_cache

@lru_cache(maxsize=2000)
def _generate_embedding_cached(text: str) -> tuple[float, ...]:
    if not text or not text.strip():
        return (0.0,) * VECTOR_DIMENSION

    try:
        model = get_embedding_model()
        if model is not None:
            embedding = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
            return tuple(embedding.tolist())
    except Exception as exc:
        logger.warning("SentenceTransformer encoding failed (%s), using hashing fallback vector.", exc)

    import hashlib, math
    vec = [0.0] * VECTOR_DIMENSION
    tokens = text.lower().split()
    for token in tokens:
        idx = int(hashlib.md5(token.encode('utf-8')).hexdigest(), 16) % VECTOR_DIMENSION
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return tuple(x / norm for x in vec)


def generate_embedding(text: str) -> list[float]:
    """Generate a 384-dimensional dense vector embedding for the given text with LRU caching."""
    return list(_generate_embedding_cached(text))

