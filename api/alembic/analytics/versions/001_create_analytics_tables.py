"""create analytics tables per contract.json

Revision ID: 001
Revises:
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("customer_code", sa.String(20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(20), nullable=False),
        sa.Column("segment", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "product_categories",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("category_code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("sku", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category_id", sa.BigInteger, sa.ForeignKey("product_categories.id"), nullable=False),
        sa.Column("list_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("order_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", sa.BigInteger, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(5, 4), nullable=False),
        sa.UniqueConstraint("order_id", "product_id"),
    )

    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_reader")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO analytics_reader")


def downgrade():
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("product_categories")
    op.drop_table("customers")
