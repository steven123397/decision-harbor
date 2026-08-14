.PHONY: up down test validate-dataset clean

COMPOSE := docker compose

up:
	$(COMPOSE) up -d --build db api web
	./scripts/wait-ready.sh

down:
	$(COMPOSE) down

test: up
	$(COMPOSE) run --rm api pytest -q
	$(COMPOSE) run --rm web npm run test:unit
	$(COMPOSE) --profile e2e run --rm e2e npx playwright test

validate-dataset:
	cd datasets/sales-analytics-v1 && python3 validate.py

clean:
	$(COMPOSE) down -v
