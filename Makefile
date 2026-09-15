CRM_DIR      := backend/crm
CHANNEL_DIR  := backend/channel
FRONTEND_DIR := frontend

.PHONY: help up down logs seed lint lint-crm lint-channel lint-frontend test test-crm test-channel

help:          ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up:            ## Start the backend stack via docker compose
	docker compose up --build -d

down:          ## Stop the backend stack
	docker compose down

logs:          ## Tail logs for all services
	docker compose logs -f

seed:          ## Seed Supabase with fake data
	cd $(CRM_DIR) && python db/seed.py

lint-crm:      ## Lint CRM backend
	cd $(CRM_DIR) && ruff check .

lint-channel:  ## Lint Channel service
	cd $(CHANNEL_DIR) && ruff check .

lint-frontend: ## Lint Next.js frontend
	cd $(FRONTEND_DIR) && npm run lint

lint: lint-crm lint-channel lint-frontend  ## Lint everything

test-crm:      ## Run CRM tests
	cd $(CRM_DIR) && python -m pytest -v

test-channel:  ## Run Channel service tests
	cd $(CHANNEL_DIR) && python -m pytest -v

test: test-crm test-channel  ## Run all backend tests
