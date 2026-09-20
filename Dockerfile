FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir . && useradd --create-home safeops
USER safeops
EXPOSE 8000
CMD ["safeops", "serve"]
