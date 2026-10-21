FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Os dados (banco SQLite) ficam em /srv/data: monte um volume persistente ali.
ENV DB_PATH=/srv/data/sonar.db
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
