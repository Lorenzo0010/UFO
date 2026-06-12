# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Dipendenze di sistema per Chromium (Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
    libdrm2 libgbm1 libgtk-3-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libxshmfence1 libasound2 libpango-1.0-0 \
    libpangocairo-1.0-0 libx11-xcb1 libxcb-dri3-0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installa solo Chromium (senza WebKit/Firefox)
RUN playwright install chromium

# Copia il codice sorgente
COPY . .

EXPOSE 8080

CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8080"]
