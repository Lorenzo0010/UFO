# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc/VixCloud**, **VidXgo** e **AltadefinizioneStreaming**.
> Supporta il deploy su Koyeb, Render o VPS tramite Docker.

---

## ⚠️ Disclaimer

**Questo progetto è realizzato esclusivamente a scopo educativo e di ricerca.**

L'autore non è responsabile di alcun utilizzo improprio, illegale o non autorizzato del presente software.
Utilizzando questo progetto, l'utente accetta di assumersi la piena responsabilità delle proprie azioni e di rispettare le leggi vigenti nel proprio paese.

- Questo addon **non ospita, non distribuisce e non indicizza** alcun contenuto multimediale
- Funziona esclusivamente come **proxy di reindirizzamento** verso sorgenti di terze parti pubblicamente accessibili
- L'autore **non ha alcun controllo** sui contenuti forniti da sorgenti esterne (VixSrc, VidXgo, TMDB, ecc.)
- L'autore **non garantisce** la disponibilità, la legalità o la qualità dei contenuti raggiungibili tramite questo software
- È responsabilità dell'utente verificare che l'utilizzo di questo software sia conforme alle leggi del proprio paese

> **L'autore declina ogni responsabilità civile e penale derivante dall'uso di questo software.**

---

## 📚 Scopo educativo

Questo progetto nasce come studio pratico dei seguenti argomenti:

- Sviluppo di API REST con **FastAPI** e Python asincrono
- Integrazione con API di terze parti (**TMDB API**)
- Architettura di addon per **Stremio** e il relativo protocollo
- Deploy su piattaforma cloud moderna (**Koyeb**)
- Strutturazione di progetti Dockerizzati

---

## 🚀 Come funziona

UFO aggrega stream da tre provider in parallelo.
Il backend in Python orchestrato da FastAPI esegue lo scraping e il fetch dai provider direttamente dal server. 
Utilizza un **proxy HLS interno** che riscrive i manifest (m3u8) e inoltra i segmenti video per bypassare limitazioni CORS e blocchi regionali tipici dei browser e dei player TV.

---

## 📥 Installazione e Utilizzo

Poiché l'addon richiede un server proxy per bypassare i CORS e riscrivere i flussi video, devi eseguirlo su un server (o in locale):

1. Fai il deploy del codice su un servizio di hosting (es. Koyeb, Render, VPS con Docker).
2. Apri l'URL della tua istanza nel browser (es. `https://mio-ufo-server.koyeb.app`).
3. Clicca sul pulsante **"Install on Stremio"** o copia il link e incollalo nella barra di ricerca degli Addon di Stremio.

---

## 📂 Struttura del progetto

```text
UFO/
├── api/                  # Backend Python per Stremio (FastAPI)
├── Dockerfile            # Immagine Docker per deploy Stremio su VPS
├── Procfile              # Avvio per Koyeb: uvicorn api.index:app --port $PORT
├── requirements.txt      # Dipendenze Python
└── README.md
```

---

## 💻 Sviluppo

```bash
# Installa le dipendenze Python
pip install -r requirements.txt

# Crea il file .env per le variabili d'ambiente
cp .env.example .env

# Avvia il server di sviluppo (FastAPI)
uvicorn api.index:app --reload --port 8080
```


