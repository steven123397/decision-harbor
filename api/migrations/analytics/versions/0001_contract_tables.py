"""按数据契约建立 analytics 契约五表并授权只读身份。

字段与约束忠实映射 datasets/sales-analytics-v1/contract.json，
不改名、不增删业务字段；CHECK 约束落实契约的 allowed_values 与业务边界。
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "analytics"


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("customer_code", sa.String(20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(20), nullable=False),
        sa.Column("segment", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "region IN ('Central', 'East', 'North', 'South', 'West')",
            name="customers_region_allowed",
        ),
        sa.CheckConstraint(
            "segment IS NULL OR segment IN ('enterprise', 'mid_market', 'small_business')",
            name="customers_segment_allowed",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "product_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("category_code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sku", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "category_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.product_categories.id"),
            nullable=False,
        ),
        sa.Column("list_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "cost_price <= list_price", name="products_cost_not_above_list_price"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_no", sa.String(30), nullable=False, unique=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.customers.id"),
            nullable=False,
        ),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.CheckConstraint(
            "status IN ('cancelled', 'confirmed', 'pending', 'refunded')",
            name="orders_status_allowed",
        ),
        sa.CheckConstraint("currency = 'CNY'", name="orders_currency_allowed"),
        schema=SCHEMA,
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.orders.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.products.id"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(5, 4), nullable=False),
        sa.UniqueConstraint("order_id", "product_id", name="order_items_order_product_unique"),
        sa.CheckConstraint(
            "discount_rate >= 0 AND discount_rate <= 1",
            name="order_items_discount_rate_range",
        ),
        schema=SCHEMA,
    )

    # 只读身份授权：schema 使用权 + 契约表与迁移版本表的 SELECT
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO analytics_reader")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO analytics_reader")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM analytics_reader")
    op.execute(f"REVOKE USAGE ON SCHEMA {SCHEMA} FROM analytics_reader")
    for table in ("order_items", "orders", "products", "product_categories", "customers"):
        op.drop_table(table, schema=SCHEMA)
