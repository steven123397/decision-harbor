"""数据契约中与策略相关的固定事实。

权威来源是 datasets/sales-analytics-v1/contract.json；契约版本变化时只更新此处。
"""

ANALYTICS_SCHEMA = "analytics"

CONTRACT_TABLES = frozenset(
    {
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
    }
)
