"""契约 → DDL 派生：CHECK 约束进入数据库（design/data-and-seeding.md）。"""

from __future__ import annotations

from app.seed.loader import build_create_table

RULES = {
    "discount_rate_range": [0, 1],
    "product_cost_not_above_list_price": True,
}


def test_allowed_values_become_check_constraints():
    ddl = build_create_table(
        {
            "name": "customers",
            "columns": [
                {"name": "id", "type": "bigint", "nullable": False, "primary_key": True},
                {
                    "name": "region",
                    "type": "varchar(20)",
                    "nullable": False,
                    "allowed_values": ["Central", "East"],
                },
            ],
        },
        RULES,
    )
    assert (
        'CONSTRAINT "ck_customers_region_allowed" '
        'CHECK ("region" IN (\'Central\', \'East\'))' in ddl
    )


def test_discount_range_and_price_rules_become_check_constraints():
    items = build_create_table(
        {
            "name": "order_items",
            "columns": [
                {"name": "id", "type": "bigint", "nullable": False, "primary_key": True},
                {"name": "discount_rate", "type": "numeric(5,4)", "nullable": False},
            ],
        },
        RULES,
    )
    assert (
        'CONSTRAINT "ck_order_items_discount_rate_range" '
        'CHECK ("discount_rate" BETWEEN 0.0 AND 1.0)' in items
    )

    products = build_create_table(
        {
            "name": "products",
            "columns": [
                {"name": "id", "type": "bigint", "nullable": False, "primary_key": True},
                {"name": "list_price", "type": "numeric(12,2)", "nullable": False},
                {"name": "cost_price", "type": "numeric(12,2)", "nullable": False},
            ],
        },
        RULES,
    )
    assert (
        'CONSTRAINT "ck_products_cost_not_above_list" '
        'CHECK ("cost_price" <= "list_price")' in products
    )


def test_single_quotes_in_values_are_escaped():
    ddl = build_create_table(
        {
            "name": "t",
            "columns": [
                {"name": "v", "type": "varchar(10)", "allowed_values": ["it's"]},
            ],
        },
        {},
    )
    assert "IN ('it''s')" in ddl
