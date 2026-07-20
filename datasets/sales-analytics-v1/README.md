# Sales Analytics Dataset v1

This directory contains a deterministic, synthetic sales dataset for a small
PostgreSQL-backed analytics product. It contains no real personal information
and is intentionally independent of any third-party sample database.

## Contents

- `contract.json` is the machine-readable schema and business contract.
- `data/` contains the committed CSV fixtures used by the public dataset.
- `generate.py` recreates the fixtures with the fixed default seed. It accepts
  an alternate seed for generating another dataset with the same contract.
- `validate.py` checks the committed files, relationships, business bounds,
  expected distributions, and deterministic regeneration.
- `manifest.json` records the dataset version, seed, row counts, and hashes.

The contract has five tables: `customers`, `product_categories`, `products`,
`orders`, and `order_items`. The public fixture contains 100 customers, 8
categories, 50 products, 1,000 orders, and 3,000 order items. Orders cover
`2024-01-01` through `2025-12-31` and use `CNY`.

## Recreate and validate

From this directory:

```text
python generate.py
python validate.py
```

The generator uses only the Python standard library. The committed CSV files
are the authoritative public fixture; the generator and manifest make changes
detectable and reproducible.
