"""按 spec 紧凑 JSON 口径计算有限结果快照。"""

import json
from dataclasses import dataclass

from decisionharbor.domain import QueryResult


SNAPSHOT_MAX_ROWS = 500
SNAPSHOT_MAX_BYTES = 1_048_576


class SnapshotTooLarge(Exception):
    """快照结构性地超出字节预算，不能保存任何部分行。"""


@dataclass(frozen=True)
class BuiltSnapshot:
    payload: str
    row_count: int
    byte_size: int
    truncated: bool


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_snapshot(
    result: QueryResult,
    *,
    max_rows: int = SNAPSHOT_MAX_ROWS,
    max_bytes: int = SNAPSHOT_MAX_BYTES,
) -> BuiltSnapshot:
    """保存数据库返回顺序的最长合法前缀；结构性越界抛出 SnapshotTooLarge。

    字节数按 {"columns":[...],"rows":[...]} 的紧凑 UTF-8 JSON 计算：键序固定，
    列对象字段序固定为 name、type，分隔符不含空格，非 ASCII 直接编码为 UTF-8。
    """
    columns = [{"name": column.name, "type": column.type} for column in result.columns]
    columns_json = _dumps(columns)
    base_parts = ('{"columns":', columns_json, ',"rows":[', "]}")
    base_bytes = sum(len(part.encode("utf-8")) for part in base_parts)
    if base_bytes > max_bytes:
        raise SnapshotTooLarge()

    row_limit = min(max_rows, SNAPSHOT_MAX_ROWS)
    kept: list[str] = []
    total_bytes = base_bytes
    truncated = result.truncated

    for row in result.rows:
        if len(kept) >= row_limit:
            truncated = True
            break
        row_json = _dumps(list(row))
        row_bytes = len(row_json.encode("utf-8"))
        if row_bytes > max_bytes:
            raise SnapshotTooLarge()
        separator_bytes = 0 if not kept else 1
        if total_bytes + separator_bytes + row_bytes > max_bytes:
            truncated = True
            break
        kept.append(row_json)
        total_bytes += separator_bytes + row_bytes

    if not kept and result.rows:
        raise SnapshotTooLarge()

    payload = '{"columns":' + columns_json + ',"rows":[' + ",".join(kept) + "]}"
    return BuiltSnapshot(
        payload=payload,
        row_count=len(kept),
        byte_size=total_bytes,
        truncated=truncated,
    )
