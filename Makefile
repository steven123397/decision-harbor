# DecisionHarbor 本地运行与测试入口（语义见 docs/design/testing.md）。

COMPOSE_FILE := deploy/compose.yaml
API_PORT ?= 8081
WEB_PORT ?= 8080
API_URL := http://localhost:$(API_PORT)
WEB_URL := http://localhost:$(WEB_PORT)
COMPOSE := docker compose --env-file .env -f $(COMPOSE_FILE)

.PHONY: up down test unit-test integration-test web-test browser-test dataset-validate

up:
	$(COMPOSE) up --build --wait

down:
	$(COMPOSE) down

test: unit-test integration-test web-test browser-test

unit-test:
	$(COMPOSE) exec -T api pytest tests/unit -q

# 集成测试需要 owner 身份做负向用例，运行在一次性 api-test 容器里。
integration-test:
	$(COMPOSE) --profile test run --rm api-test

# Web 单元测试运行在带 Node 工具链的 web-test 镜像，不进生产容器。
web-test:
	$(COMPOSE) --profile test run --rm web-test

browser-test:
	cd web && WEB_URL=$(WEB_URL) npx playwright test

dataset-validate:
	cd datasets/sales-analytics-v1 && python3 validate.py
