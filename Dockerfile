FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt
COPY app ./app
COPY cypher ./cypher
COPY pipeline/db.py pipeline/normalise.py ./pipeline/
EXPOSE 8080
CMD ["sh", "-c", "uvicorn server:app --app-dir app --host 0.0.0.0 --port ${PORT:-8080}"]
