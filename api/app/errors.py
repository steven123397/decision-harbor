"""统一错误信封与业务异常。对应 docs/design/api-and-workbench.md 的错误语义。"""

from __future__ import annotations

INVALID_REQUEST = "INVALID_REQUEST"
RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
