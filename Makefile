.PHONY: help install dev lint format test integration-test coverage build run clean

# ─────────────────────────────────────────────
# HELP
# ─────────────────────────────────────────────
help:
	@echo "FairFare Ingestion Service"
	@echo ""
	@echo "Usage: make <command>"
	@echo ""
	@echo "  install           Installer les dépendances"
	@echo "  dev               Lancer en mode développement (hot reload)"
	@echo "  lint              Vérifier le code (ruff check)"
	@echo "  format            Formater le code (ruff format)"
	@echo "  test              Lancer les tests unitaires"
	@echo "  integration-test  Lancer les tests d'intégration"
	@echo "  coverage          Lancer les tests avec rapport de couverture"
	@echo "  build             Builder l'image Docker"
	@echo "  run               Lancer en mode production"
	@echo "  clean             Nettoyer les artefacts de build"

# ─────────────────────────────────────────────
# INSTALLATION
# ─────────────────────────────────────────────
install:
	pip install -e .
	pip install -e ".[dev]"

# ─────────────────────────────────────────────
# DÉVELOPPEMENT
# ─────────────────────────────────────────────
dev:
	ENVIRONMENT=dev python -m uvicorn main:app \
		--app-dir src \
		--host 0.0.0.0 \
		--port 8000 \
		--reload

# ─────────────────────────────────────────────
# QUALITÉ DU CODE
# ─────────────────────────────────────────────
lint:
	ruff check src tests
	ruff format --check src tests

format:
	ruff format src tests

# ─────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────
test:
	pytest tests/unit -v

integration-test:
	pytest tests/integration -v

coverage:
	pytest tests/ --cov=src --cov-report=html --cov-report=term
	@echo "Rapport de couverture généré dans htmlcov/index.html"

# ─────────────────────────────────────────────
# DOCKER
# ─────────────────────────────────────────────
build:
	docker build -t fairfare/ff-ingestion:latest .

# ─────────────────────────────────────────────
# PRODUCTION
# ─────────────────────────────────────────────
run:
	ENVIRONMENT=prod python -m uvicorn main:app \
		--app-dir src \
		--host 0.0.0.0 \
		--port 8000

# ─────────────────────────────────────────────
# NETTOYAGE
# ─────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build dist .pytest_cache .coverage htmlcov