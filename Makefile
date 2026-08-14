# DecisionHarbor 本地运行与测试入口（语义见 docs/design/testing.md）。

COMPOSE_FILE := deploy/compose.yaml
API_PORT ?= 8081
WEB_PORT ?= 8080
API_URL := http://localhost:$(API_PORT)
WEB_URL := http://localhost:$(WEB_PORT)

.PHONY: up down test unit-test integration-test web-test browser-test dataset-validate

up:
	docker compose --env-file .env -f $(COMPOSE_FILE) up --build --wait

down:
	docker compose --env-file .env -f $(COMPOSE_FILE) down

test: unit-test integration-test web-test browser-test

unit-test:
	docker compose --env-file .env -f $(COMPOSE_FILE) exec -T api \
		pytest tests/unit -q

integration-test:
	docker compose --env-file .env -f $(COMPOSE_FILE) exec -T api \
		pytest tests/integration -q

web-test:
	docker compose --env-file .env -f $(COMPOSE_FILE) exec -T web npm test -- --run

browser-test:
	cd web && npx playwright test

dataset-validate:
	cd datasets/sales-analytics-v1 && python3 validate.py
