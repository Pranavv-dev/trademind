.PHONY: dev build down migrate test lint clean logs1

# Development
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

build:
	docker compose build

down:
	docker compose down

# Safe restart — preserves Postgres and Redis volumes (no data loss, no re-auth needed)
restart:
	docker compose restart backend celery-worker celery-beat

# Database
migrate:
	docker compose exec backend alembic upgrade head

migration:
	docker compose exec backend alembic revision --autogenerate -m "$(msg)"

# Testing
test:
	docker compose exec backend pytest -v --tb=short

test-cov:
	docker compose exec backend pytest --cov=app --cov-report=term-missing

# Linting
lint:
	docker compose exec backend ruff check app/
	docker compose exec backend ruff format --check app/

format:
	docker compose exec backend ruff check --fix app/
	docker compose exec backend ruff format app/

# Logs
logs:
	docker compose logs -f

logs-backend:
	docker compose logs -f backend

logs-celery:
	docker compose logs -f celery-worker celery-beat

# Utilities
shell:
	docker compose exec backend python -c "import IPython; IPython.start_ipython()" 2>/dev/null || docker compose exec backend python

db-shell:
	docker compose exec db psql -U trademind -d trademind

redis-cli:
	docker compose exec redis redis-cli

# Clean
clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
