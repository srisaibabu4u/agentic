# One image for the migrate job, the API and the worker. Build from the repo root:
#   docker build -f backend/Dockerfile -t kyc-backend .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
# anthropic: only used when an agent runs live (python -m kyc.worker --live ...)
RUN pip install -r backend/requirements.txt "anthropic>=0.40"

# the master, agents and prompts are the prototype's own code; the seed reads data/sample_case.json
COPY orchestrator/ orchestrator/
COPY prompts/ prompts/
COPY data/sample_case.json data/sample_case.json
COPY backend/ backend/

WORKDIR /app/backend
RUN useradd --create-home --uid 10001 kyc && mkdir -p data/generated data/uploads && chown -R kyc data
USER kyc

EXPOSE 8000
CMD ["uvicorn", "kyc.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
