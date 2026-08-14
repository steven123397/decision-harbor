"""Create the fixed sales analytics schema."""

from alembic import op
import sqlalchemy as sa


revision = "analytics_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("customer_code", sa.String(20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(20), nullable=False),
        sa.Column("segment", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("region IN ('Central', 'East', 'North', 'South', 'West')", name="customers_region_check"),
        sa.CheckConstraint("segment IS NULL OR segment IN ('enterprise', 'mid_market', 'small_business')", name="customers_segment_check"),
        schema="analytics",
    )
    op.create_table(
        "product_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("category_code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sku", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("list_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["analytics.product_categories.id"]),
        sa.CheckConstraint("list_price > 0", name="products_list_price_check"),
        sa.CheckConstraint("cost_price > 0 AND cost_price <= list_price", name="products_cost_price_check"),
        schema="analytics",
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["analytics.customers.id"]),
        sa.CheckConstraint("status IN ('cancelled', 'confirmed', 'pending', 'refunded')", name="orders_status_check"),
        sa.CheckConstraint("currency = 'CNY'", name="orders_currency_check"),
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
        sa.CheckConstraint("quantity > 0", name="order_items_quantity_check"),
        sa.CheckConstraint("unit_price > 0", name="order_items_unit_price_check"),
        sa.CheckConstraint("discount_rate >= 0 AND discount_rate <= 1", name="order_items_discount_rate_check"),
        schema="analytics",
    )
    op.execute("GRANT USAGE ON SCHEMA analytics TO analytics_reader, analytics_readiness")
    op.execute("GRANT SELECT ON analytics.customers, analytics.product_categories, analytics.products, analytics.orders, analytics.order_items TO analytics_reader")
    op.execute("GRANT SELECT ON analytics.alembic_version TO analytics_readiness")


def downgrade() -> None:
    op.execute("REVOKE ALL ON analytics.customers, analytics.product_categories, analytics.products, analytics.orders, analytics.order_items FROM analytics_reader")
    op.execute("REVOKE ALL ON analytics.alembic_version FROM analytics_readiness")
    op.drop_table("order_items", schema="analytics")
    op.drop_table("orders", schema="analytics")
    op.drop_table("products", schema="analytics")
    op.drop_table("product_categories", schema="analytics")
    op.drop_table("customers", schema="analytics")
