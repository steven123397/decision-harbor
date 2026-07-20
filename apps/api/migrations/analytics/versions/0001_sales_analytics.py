"""Create the fixed sales analytics contract and seed metadata."""

from alembic import op


revision = "analytics_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA analytics")
    op.execute("CREATE SCHEMA maintenance")
    op.execute(
        """
        CREATE TABLE analytics.customers (
            id bigint PRIMARY KEY,
            customer_code varchar(20) NOT NULL UNIQUE,
            display_name varchar(120) NOT NULL,
            region varchar(20) NOT NULL CHECK (region IN ('Central', 'East', 'North', 'South', 'West')),
            segment varchar(20) CHECK (segment IN ('enterprise', 'mid_market', 'small_business')),
            created_at timestamptz NOT NULL
        );
        CREATE TABLE analytics.product_categories (
            id bigint PRIMARY KEY,
            category_code varchar(20) NOT NULL UNIQUE,
            name varchar(80) NOT NULL
        );
        CREATE TABLE analytics.products (
            id bigint PRIMARY KEY,
            sku varchar(30) NOT NULL UNIQUE,
            name varchar(120) NOT NULL,
            category_id bigint NOT NULL REFERENCES analytics.product_categories(id),
            list_price numeric(12,2) NOT NULL CHECK (list_price > 0),
            cost_price numeric(12,2) NOT NULL CHECK (cost_price > 0 AND cost_price <= list_price),
            active boolean NOT NULL
        );
        CREATE TABLE analytics.orders (
            id bigint PRIMARY KEY,
            order_no varchar(30) NOT NULL UNIQUE,
            customer_id bigint NOT NULL REFERENCES analytics.customers(id),
            ordered_at timestamptz NOT NULL,
            status varchar(20) NOT NULL CHECK (status IN ('cancelled', 'confirmed', 'pending', 'refunded')),
            currency char(3) NOT NULL CHECK (currency = 'CNY')
        );
        CREATE TABLE analytics.order_items (
            id bigint PRIMARY KEY,
            order_id bigint NOT NULL REFERENCES analytics.orders(id),
            product_id bigint NOT NULL REFERENCES analytics.products(id),
            quantity integer NOT NULL CHECK (quantity > 0),
            unit_price numeric(12,2) NOT NULL CHECK (unit_price > 0),
            discount_rate numeric(5,4) NOT NULL CHECK (discount_rate >= 0 AND discount_rate <= 1),
            UNIQUE (order_id, product_id)
        );
        CREATE TABLE maintenance.dataset_seeds (
            dataset text NOT NULL,
            version text NOT NULL,
            contract_sha256 char(64) NOT NULL,
            manifest_sha256 char(64) NOT NULL,
            row_counts jsonb NOT NULL,
            seeded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (dataset, version)
        )
        """
    )
    op.execute("REVOKE ALL ON SCHEMA analytics, maintenance FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA analytics, maintenance TO analytics_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analytics_reader")
    op.execute("GRANT SELECT ON TABLE maintenance.dataset_seeds TO analytics_reader")
    op.execute("GRANT SELECT ON TABLE alembic_version TO analytics_reader")


def downgrade() -> None:
    op.execute("DROP SCHEMA maintenance CASCADE")
    op.execute("DROP SCHEMA analytics CASCADE")
