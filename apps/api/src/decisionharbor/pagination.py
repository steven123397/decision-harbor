"""历史分页合同：不透明游标编解码与 limit 边界。"""

import base64
import json
from datetime import datetime
from uuid import UUID

from decisionharbor.domain import HistoryCursor


DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# 游标内容的版本号：内部格式演进时递增，旧游标按非法处理。
CURSOR_VERSION = 1


def encode_cursor(cursor: HistoryCursor) -> str:
    """把键集位置编码为不透明、URL 安全的字符串。"""
    payload = json.dumps(
        {
            "v": CURSOR_VERSION,
            "created_at": cursor.created_at.isoformat(),
            "id": cursor.id,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> HistoryCursor | None:
    """解析服务端生成的游标；任何非本服务形态的输入都返回 None。"""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        decoded = json.loads(payload)
        version = decoded["v"]
        created_at = datetime.fromisoformat(decoded["created_at"])
        run_id = str(UUID(decoded["id"]))
    except (ValueError, KeyError, TypeError):
        return None
    if version != CURSOR_VERSION or created_at.tzinfo is None:
        return None
    return HistoryCursor(created_at=created_at, id=run_id)


def parse_limit(raw: str | None) -> int | None:
    """limit 默认 20、范围 1 到 100；越界或非法返回 None。"""
    if raw is None:
        return DEFAULT_PAGE_LIMIT
    # 只接受 ASCII 十进制：空白、正负号、指数与非 ASCII 数字都按非法处理。
    if not raw.isascii() or not raw.isdigit():
        return None
    limit = int(raw)
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        return None
    return limit
