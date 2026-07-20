FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# tiktoken otherwise downloads cl100k_base on first use. Baking it into the
# image keeps document restore and chunking independent of the public blob CDN.
ENV TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host ${SERVER_HOST:-127.0.0.1} --port 8000"]
