# Usa Python 3.10
FROM python:3.10-slim

# Imposta la cartella di lavoro principale
WORKDIR /app

# Copia il file dei requisiti dalla root e installa
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia TUTTO il contenuto del repo (inclusa la cartella api) dentro /app
COPY . .

# Crea l'utente per Hugging Face (sicurezza)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Espone la porta 7860
EXPOSE 7860

# AVVIO:
# Nota il cambio qui: "api.index:app"
# Dice a uvicorn di cercare nella cartella 'api', il file 'index', l'oggetto 'app'
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "7860"]
