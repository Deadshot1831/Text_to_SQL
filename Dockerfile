FROM python:3.11-slim

WORKDIR /app

# psycopg[binary] ships wheels, so no build deps needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY db ./db
COPY evals ./evals
COPY frontend ./frontend

EXPOSE 8000 8501

# Default: the API. docker-compose overrides command for the frontend service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
