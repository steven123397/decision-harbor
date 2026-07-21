SHELL := /bin/bash
.PHONY: up down restart ps logs validate-dataset test test-unit test-integration test-e2e clean

up:
	docker compose up -d --build
	@bash scripts/wait-ready.sh

down:
	docker compose down

restart:
	docker compose restart

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=200

validate-dataset:
	cd datasets/sales-analytics-v1 && python3 validate.py

test: test-unit test-integration test-e2e

test-unit:
	docker compose run --rm --no-deps -e DH_SKIP_BOOTSTRAP=1 api pytest tests/test_policy.py -q

test-integration:
	docker compose up -d --build db
	docker compose run --rm api pytest tests/test_integration.py -q

test-e2e:
	docker compose up -d --build
	@bash scripts/wait-ready.sh
	cd web && npx playwright test

clean:
	docker compose down -v
