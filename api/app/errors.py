from __future__ import annotations

import re


class ExecutionFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def summarize_error(exc: BaseException) -> str:
    text = str(exc)
    text = re.sub(r"postgresql(\+[a-z]+)?:[^\s]+", "[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"password=[^\s]+", "password=[redacted]", text, flags=re.IGNORECASE)
    return text[:500]
