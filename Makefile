.PHONY: install dev redis stop-redis demo test test-e2e lint format clean

install:
	uv sync

dev:
	uv run uvicorn agent_bus.main:app --host 0.0.0.0 --port 18080 --reload

redis:
	docker compose up -d redis

stop-redis:
	docker compose down

demo:
	@python examples/client_demo.py

test:
	uv run pytest tests/ -v

test-e2e:
	uv run pytest tests/test_e2e.py -v

lint:
	uv run ruff check agent_bus examples
	uv run pyright agent_bus

format:
	uv run ruff format agent_bus examples
	uv run ruff check --fix agent_bus examples

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '.pytest_cache' -delete 2>/dev/null || true
	rm -rf .ruff_cache .pyright 2>/dev/null || true
