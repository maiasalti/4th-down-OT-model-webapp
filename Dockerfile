FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cached layer). Runtime needs only Flask, numpy and the
# CPU-only XGBoost wheel; see requirements-dev.txt for training tools.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the server needs at runtime.
COPY gunicorn.conf.py server.py decision_engine.py models.py ./
COPY models ./models
COPY static ./static
COPY templates ./templates

EXPOSE 10000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "server:app"]
