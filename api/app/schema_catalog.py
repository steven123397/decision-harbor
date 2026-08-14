"""Authorized analytics objects derived from contract.json (single source of truth)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _pg_type(type_name: str) -> str:
    if type_name == "bigint":
        return "BIGINT"
    if type_name == "integer":
        return "INTEGER"
    if type_name == "boolean":
        return "BOOLEAN"
    if type_name == "timestamptz":
        return "TIMESTAMPTZ"
    if type_name.startswith("varchar") or type_name.startswith("numeric") or type_name.startswith("char"):
        return type_name.upper()
    raise ValueError(f"unknown contract type: {type_name}")


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str
    nullable: bool
    primary_key: bool = False
    unique: bool = False


@dataclass(frozen=True)
class Reference:
    column: str
    table: str
    target_column: str


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    references: tuple[Reference, ...]
    unique_constraints: tuple[tuple[str, ...], ...]


class Catalog:
    def __init__(self, contract: dict[str, Any]) -> None:
        self.version: str = contract["version"]
        self.schema: str = contract.get("schema", "analytics")
        self.tables: dict[str, Table] = {}
        for table in contract["tables"]:
            columns = tuple(
                Column(
                    name=col["name"],
                    pg_type=_pg_type(col["type"]),
                    nullable=col.get("nullable", False),
                    primary_key=col.get("primary_key", False),
                    unique=col.get("unique", False),
                )
                for col in table["columns"]
            )
            references = tuple(
                Reference(ref["column"], ref["table"], ref["target_column"])
                for ref in table.get("references", [])
            )
            unique_constraints = tuple(
                tuple(constraint) for constraint in table.get("unique_constraints", [])
            )
            self.tables[table["name"]] = Table(table["name"], columns, references, unique_constraints)

    @property
    def table_names(self) -> frozenset[str]:
        return frozenset(self.tables)

    def load_order(self) -> list[str]:
        """Return table names in dependency (foreign key) order."""
        order: list[str] = []
        remaining = set(self.tables)
        while remaining:
            ready = sorted(
                name
                for name in remaining
                if all(ref.table not in remaining for ref in self.tables[name].references)
            )
            if not ready:
                raise ValueError("circular reference detected in contract.json")
            for name in ready:
                order.append(name)
                remaining.discard(name)
        return order

    def create_table_sql(self, name: str) -> str:
        table = self.tables[name]
        lines: list[str] = []
        for col in table.columns:
            definition = f'"{col.name}" {col.pg_type}'
            if col.primary_key:
                definition += " PRIMARY KEY"
            if col.unique:
                definition += " UNIQUE"
            if not col.nullable:
                definition += " NOT NULL"
            lines.append(definition)
        for ref in table.references:
            lines.append(
                f'FOREIGN KEY ("{ref.column}") REFERENCES "{self.schema}"."{ref.table}"("{ref.target_column}")'
            )
        for constraint in table.unique_constraints:
            lines.append("UNIQUE (" + ", ".join(f'"{col}"' for col in constraint) + ")")
        body = ",\n  ".join(lines)
        return f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{name}" (\n  {body}\n)'


def load_catalog(contract_path: Path | str) -> Catalog:
    with open(contract_path, encoding="utf-8") as stream:
        return Catalog(json.load(stream))
