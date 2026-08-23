"""按 spec 紧凑 JSON 口径计算有限结果快照。"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from decisionharbor.domain import JsonCell, QueryColumn


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


class SnapshotBuilder:
    """增量累计快照字节预算；预算耗尽后调用方应立即停止读取游标。

    字节数按 {"columns":[...],"rows":[...]} 的紧凑 UTF-8 JSON 计算：键序固定，
    列对象字段序固定为 name、type，分隔符不含空格，非 ASCII 直接编码为 UTF-8。
    envelope、运行元数据与 truncated 不计入预算。
    """

    def __init__(self, columns: Sequence[QueryColumn], *, max_rows: int = SNAPSHOT_MAX_ROWS) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be a positive integer")
        self._max_bytes = SNAPSHOT_MAX_BYTES
        self._row_limit = min(max_rows, SNAPSHOT_MAX_ROWS)
        self._columns_json = _dumps([{"name": column.name, "type": column.type} for column in columns])
        self._base_bytes = (
            len('{"columns":'.encode("utf-8"))
            + len(self._columns_json.encode("utf-8"))
            + len(',"rows":['.encode("utf-8"))
            + len("]}".encode("utf-8"))
        )
        if self._base_bytes > self._max_bytes:
            raise SnapshotTooLarge()
        self._kept: list[str] = []
        self._total_bytes = self._base_bytes
        self._truncated = False
        self._exhausted = False

    def add_row(self, row: Sequence[JsonCell]) -> bool:
        """加入一行；返回 False 表示预算已耗尽，调用方应停止读取后续行。"""
        if self._exhausted:
            return False
        if len(self._kept) >= self._row_limit:
            self._truncated = True
            self._exhausted = True
            return False
        row_json = _dumps(list(row))
        row_bytes = len(row_json.encode("utf-8"))
        if row_bytes > self._max_bytes:
            raise SnapshotTooLarge()
        separator_bytes = 1 if self._kept else 0
        if self._total_bytes + separator_bytes + row_bytes > self._max_bytes:
            if not self._kept:
                # 首行加入后整体即超限：结构性越界，不保存任何部分行。
                raise SnapshotTooLarge()
            self._truncated = True
            self._exhausted = True
            return False
        self._kept.append(row_json)
        self._total_bytes += separator_bytes + row_bytes
        return True

    def build(self) -> BuiltSnapshot:
        payload = '{"columns":' + self._columns_json + ',"rows":[' + ",".join(self._kept) + "]}"
        return BuiltSnapshot(
            payload=payload,
            row_count=len(self._kept),
            byte_size=self._total_bytes,
            truncated=self._truncated,
        )


def build_snapshot(
    columns: Sequence[QueryColumn],
    rows: Iterable[Sequence[JsonCell]],
    *,
    max_rows: int = SNAPSHOT_MAX_ROWS,
) -> BuiltSnapshot:
    """保存数据库返回顺序的最长合法前缀；结构性越界抛出 SnapshotTooLarge。"""
    builder = SnapshotBuilder(columns, max_rows=max_rows)
    for row in rows:
        if not builder.add_row(row):
            break
    return builder.build()
