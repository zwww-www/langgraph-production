import re
from typing import Any

SENSITIVE = re.compile(r"password|secret|api.?key|authorization|credential|access.?token", re.I)


def redact_text(text: str) -> str:
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    return re.sub(r"(?i)(password|secret|api_key)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return redact_text(value) if isinstance(value, str) else value
