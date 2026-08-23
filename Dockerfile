FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_MODE=local

WORKDIR /app

RUN addgroup --system isaacassist \
    && adduser --system --ingroup isaacassist --home /app isaacassist

COPY pyproject.toml README.md LICENSE ./
COPY service ./service

RUN python -m pip install --no-cache-dir .

USER isaacassist
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["isaac-assist-service"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
