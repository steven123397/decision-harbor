"""analytics：契约五表。

表名、字段、类型、约束严格对齐 datasets/sales-analytics-v1/contract.json，不得改名或重解释。

revision: a0001
"""

import sqlalchemy as sa
from alembic import op

revision = "a0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "analytics"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("customer_code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("segment", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "product_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("category_code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sku", sa.String(length=30), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("list_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("cost_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], [f"{SCHEMA}.product_categories.id"]
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_no", sa.String(length=30), nullable=False, unique=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], [f"{SCHEMA}.customers.id"]),
        schema=SCHEMA,
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], [f"{SCHEMA}.orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], [f"{SCHEMA}.products.id"]),
        sa.UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in ("order_items", "orders", "products", "product_categories", "customers"):
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
