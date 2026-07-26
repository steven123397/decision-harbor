"""analytics：契约业务约束。

严格兑现 contract.json 中已定义的字段类型与业务规则：
- currency 为 char(3) 且仅允许 CNY；
- 订单状态、客户区域与细分的 allowed_values；
- discount_rate 范围 0..1；
- 产品成本不高于标价。

revision: a0002
"""

import sqlalchemy as sa
from alembic import op

revision = "a0002"
down_revision = "a0001"
branch_labels = None
depends_on = None

SCHEMA = "analytics"

_CHECKS = [
    ("ck_orders_currency", "orders", "currency IN ('CNY')"),
    (
        "ck_orders_status",
        "orders",
        "status IN ('pending', 'confirmed', 'cancelled', 'refunded')",
    ),
    (
        "ck_customers_region",
        "customers",
        "region IN ('Central', 'East', 'North', 'South', 'West')",
    ),
    (
        "ck_customers_segment",
        "customers",
        "segment IS NULL OR segment IN ('enterprise', 'mid_market', 'small_business')",
    ),
    ("ck_products_cost_not_above_list", "products", "cost_price <= list_price"),
    (
        "ck_order_items_discount_rate",
        "order_items",
        "discount_rate >= 0 AND discount_rate <= 1",
    ),
]


def upgrade() -> None:
    op.alter_column(
        "orders",
        "currency",
        existing_type=sa.String(length=3),
        type_=sa.CHAR(length=3),
        schema=SCHEMA,
    )
    for name, table, condition in _CHECKS:
        op.create_check_constraint(name, table, condition, schema=SCHEMA)


def downgrade() -> None:
    for name, table, _condition in _CHECKS:
        op.drop_constraint(name, table, schema=SCHEMA)
    op.alter_column(
        "orders",
        "currency",
        existing_type=sa.CHAR(length=3),
        type_=sa.String(length=3),
        schema=SCHEMA,
    )
