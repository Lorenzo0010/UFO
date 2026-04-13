# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Installa dipendenze prima di copiare il codice (sfrutta la cache layer Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente
COPY . .

EXPOSE 8080

CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8080"]
