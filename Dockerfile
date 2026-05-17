FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WAREHOUSE_ENV=production \
    WAREHOUSE_HOST=0.0.0.0 \
    WAREHOUSE_PORT=8088 \
    WAREHOUSE_TUNNEL=0 \
    WAREHOUSE_OPEN_BROWSER=0 \
    WAREHOUSE_DATA_DIR=/app/data \
    WAREHOUSE_DATA_ENCRYPTION=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY CHANGELOG.md README.md HOW_TO_RUN.md SERVER_DEPLOY.md DOCKER_DEPLOY.md DATA_SECURITY.md WAREHOUSE_USER_MANUAL.md ./
COPY static ./static
COPY public ./public
COPY sample_bom.csv ./

RUN mkdir -p /app/data

EXPOSE 8088
VOLUME ["/app/data"]

CMD ["python", "app.py"]
