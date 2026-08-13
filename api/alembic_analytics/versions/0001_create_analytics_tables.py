"""create analytics schema and contracted tables

Revision ID: 0001_analytics
Revises:
Create Date: 2026-08-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_analytics"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.create_table(
        "product_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("category_code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("customer_code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("segment", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sku", sa.String(length=30), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("list_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["analytics.product_categories.id"]),
        schema="analytics",
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_no", sa.String(length=30), nullable=False, unique=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["analytics.customers.id"]),
        schema="analytics",
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(5, 4), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["analytics.orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["analytics.products.id"]),
        sa.UniqueConstraint("order_id", "product_id"),
        schema="analytics",
    )
    op.execute(
        "GRANT USAGE ON SCHEMA analytics TO analytics_reader"
    )
    op.execute(
        "GRANT SELECT ON analytics.customers, analytics.product_categories, "
        "analytics.products, analytics.orders, analytics.order_items TO analytics_reader"
    )


def downgrade() -> None:
    op.drop_table("order_items", schema="analytics")
    op.drop_table("orders", schema="analytics")
    op.drop_table("products", schema="analytics")
    op.drop_table("customers", schema="analytics")
    op.drop_table("product_categories", schema="analytics")
