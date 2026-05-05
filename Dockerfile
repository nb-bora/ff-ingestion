# ─────────────────────────────────────────────
# STAGE 1 — Builder
# ─────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Dépendances système de compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python (cache layer séparé)
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --user --no-cache-dir .

# ─────────────────────────────────────────────
# STAGE 2 — Production
# ─────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Utilisateur non-root
RUN useradd -m -u 1000 ff-user && \
    chown -R ff-user:ff-user /app

# Copie des packages installés depuis le builder
COPY --from=builder --chown=ff-user:ff-user /root/.local /home/ff-user/.local

# Copie du code source
COPY --chown=ff-user:ff-user src/ ./src/

# Variables d'environnement Python
ENV PATH=/home/ff-user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER ff-user

# ─────────────────────────────────────────────
# HEALTHCHECK & EXPOSITION
# ─────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/health', timeout=2); sys.exit(0 if r.status==200 else 1)" || exit 1

EXPOSE 8000

# ─────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/src"]
