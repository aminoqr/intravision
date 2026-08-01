.PHONY: help setup test demo sample-fixtures fetch fetch-live probe serve campus cursus resolve-ids clean

PY := .venv/bin/python
PIP := .venv/bin/pip
export PYTHONPATH := src

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install dependencies
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt
	@echo "done. cp .env.example .env and fill in credentials."

test: ## Run unit tests (no network, no credentials needed)
	$(PY) -m pytest tests/ -q

demo: ## Serve dashboard from fixtures/ (no API). Does NOT overwrite live Warsaw fixtures.
	$(PY) -m ft.fetch --fixtures
	$(PY) -m uvicorn ft.app:app --host 0.0.0.0 --port 8000

sample-fixtures: ## Regenerate synthetic fixtures (Warsaw coalition names). Prefer live fixtures for demos.
	$(PY) scripts/make_sample_fixtures.py

fetch: ## Rebuild metrics from fixtures/ (costs no API quota)
	$(PY) -m ft.fetch --fixtures

fetch-live: ## Fetch from the real 42 API and save fixtures
	$(PY) -m ft.fetch --save-fixtures -v

probe: ## Probe the API and write docs/api-probe-results.md (~25 requests)
	$(PY) -m ft.probe

serve: ## Run the dashboard server
	$(PY) -m uvicorn ft.app:app --host 0.0.0.0 --port 8000 --reload

campus: ## Find the Warsaw campus id
	@$(PY) -c "from ft.client import FtClient; from ft.config import Config; \
	c=Config.from_env(); \
	[print(x['id'], x['name']) for x in FtClient(c.uid,c.secret).get_json('/v2/campus',{'filter[name]':'Warsaw'})]"

cursus: ## List cursus ids (find the Common Core one)
	@$(PY) -c "from ft.client import FtClient; from ft.config import Config; \
	c=Config.from_env(); \
	[print(x['id'], x['slug']) for x in FtClient(c.uid,c.secret).get_json('/v2/cursus',{'page[size]':100})]"

resolve-ids: ## Fetch campus/cursus ids from API and write .env.local
	$(PY) scripts/resolve_env_ids.py

clean: ## Remove generated data
	rm -rf data/ .pytest_cache __pycache__ src/ft/__pycache__
