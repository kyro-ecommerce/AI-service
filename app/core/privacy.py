import re

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?84|0)(?:[ .-]?\d){9,10}(?!\d)",
)
_IDENTITY_PATTERN = re.compile(
    r"\b(?:cccd|cmnd|căn\s*cước|chứng\s*minh)\s*(?:số|:|=|-)?\s*\d{9,12}\b",
    re.IGNORECASE,
)
_LABELED_SENSITIVE_PATTERN = re.compile(
    r"\b(?:địa\s*chỉ|address|mật\s*khẩu|password|api[_ -]?key|token)\s*"
    r"(?:là|:|=|-)?\s*[^\n,;]{3,120}",
    re.IGNORECASE,
)
_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _redact_card_candidate(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19:
        return "[PAYMENT_CARD_REDACTED]"
    return match.group(0)


def redact_pii(text: str) -> str:
    """Remove common direct identifiers before persistence or external LLM calls."""
    if not text:
        return text

    redacted = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
    redacted = _IDENTITY_PATTERN.sub("[IDENTITY_REDACTED]", redacted)
    redacted = _PHONE_PATTERN.sub("[PHONE_REDACTED]", redacted)
    redacted = _CARD_CANDIDATE_PATTERN.sub(_redact_card_candidate, redacted)
    return _LABELED_SENSITIVE_PATTERN.sub("[SENSITIVE_DATA_REDACTED]", redacted)
