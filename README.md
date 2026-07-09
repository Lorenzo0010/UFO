# 🛸 UFO — Stremio Addon & Nuvio Plugin

> Addon Stremio e Plugin Nuvio che fornisce stream HLS da **VixSrc/VixCloud**, **VidXgo** e **AltadefinizioneStreaming**.
> Supporta il deploy server-side (Stremio) su Koyeb/Docker e il bundling client-side (Nuvio) tramite GitHub Actions.

---

## ⚠️ Disclaimer

**Questo progetto è realizzato esclusivamente a scopo educativo e di ricerca.**

L'autore non è responsabile di alcun utilizzo improprio, illegale o non autorizzato del presente software.
Utilizzando questo progetto, l'utente accetta di assumersi la piena responsabilità delle proprie azioni e di rispettare le leggi vigenti nel proprio paese.

- Questo addon **non ospita, non distribuisce e non indicizza** alcun contenuto multimediale
- Funziona esclusivamente come **proxy di reindirizzamento** o **scraper client-side** verso sorgenti di terze parti pubblicamente accessibili
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
- Sviluppo di plugin **Nuvio** eseguiti client-side nel browser
- Bundling di moduli Node.js tramite **esbuild**
- Deploy su piattaforma cloud moderna (**Koyeb**) e pipeline **GitHub Actions**
- Strutturazione di progetti multi-ambiente (Node.js + Python)

---

## Come funziona

UFO aggrega stream da tre provider in parallelo. Può essere utilizzato in due modalità:

### 1. Modalità Addon Stremio (Server-Side)
Il backend in Python orchestrato da FastAPI esegue il fetch dai provider direttamente dal server. Utilizza un **proxy HLS interno** che riscrive i manifest e inoltra i segmenti video per bypassare limitazioni CORS e blocchi regionali.

### 2. Modalità Plugin Nuvio (Client-Side)
Il codice Javascript contenuto in src/ viene analizzato e pacchettizzato tramite esbuild (grazie a uild.js) per generare moduli browser-compatibili caricati direttamente dal client Nuvio dell'utente (Smart TV, Telefono, PC).
Le richieste vengono effettuate nativamente tramite l'API etch del dispositivo. Solo i provider con supporto nativo e senza blocchi Cloudflare restrittivi (VixCloud, VidXgo, AltadefinizioneStreaming) sono attivati per Nuvio.

---

## Struttura del progetto

`
UFO/
├── api/                  # Backend Python per Stremio (FastAPI)
├── src/                  # Sorgenti Javascript dei provider per Nuvio
│   ├── altadefinizionestreaming/
│   ├── vidxgo/
│   ├── vixcloud/
│   ├── extractors/
│   └── utils/
├── build.js              # Script esbuild per la pacchettizzazione Nuvio
├── manifest.json         # Manifest principale del plugin Nuvio
├── package.json          # Dipendenze Node.js (esbuild, crypto-js, ecc.)
├── Dockerfile            # Immagine Docker per deploy Stremio su VPS
├── Procfile              # Avvio per Koyeb: uvicorn api.index:app --port $PORT
├── requirements.txt      # Dipendenze Python
└── README.md
`

---

## Sviluppo Plugin Nuvio

### Costruzione Locale
Assicurati di avere Node.js installato.
`ash
npm install
npm run build
`
Questo genererà la cartella providers/ contenente il codice pronto per Nuvio.

### GitHub Actions
Il repository è configurato per compilare automaticamente i provider ad ogni push sul branch main.
Nuvio andrà a leggere direttamente i file compilati dalla cartella providers/ esposta tramite GitHub Pages o Raw.

---

## Sviluppo Addon Stremio

`ash
# Installa dipendenze Python
pip install -r requirements.txt

# Crea il file .env
cp .env.example .env

# Avvia il server
uvicorn api.index:app --reload --port 8080
`

---

## 📋 Changelog

### [2.0.0] — 2026-07
> Dual-Support Nuvio e Pulizia Provider

- **feat**: Aggiunto supporto nativo al plugin **Nuvio**, con esecuzione client-side nel browser.
- **feat**: Pipeline esbuild (uild.js) e package.json integrati per raggruppare i file JS.
- **feat**: Aggiunta la CI/CD via GitHub Actions per pacchettizzare il codice su ogni push.
- **refactor**: Eliminati i provider bloccati da Cloudflare o non compatibili nativamente (Guardoserie, AnimeUnity, AnimeSaturn, NetMirror, ecc.).
- **refactor**: Mantenuti e stabilizzati i tre provider autonomi primari: **VixCloud, VidXgo e AltadefinizioneStreaming**.
- **fix**: Sostituiti moduli Node.js-only (xios, s, https) con un fallback sicuro nativo (etch) per il client browser.
- **chore**: Riordinata la lista dei provider nel manifest.json.

### [1.7.0] — 2026-06
> Rimozione EasyProxy, Fix Proxy e Porting

- **refactor**: rimossa completamente la dipendenza da EasyProxy per Stremio.
- **fix**: aggiornato Dockerfile per esporre la porta 8080.

### [1.6.0] — 2026-06
> Proxy HLS interno e VidXgo

- **feat**: proxy HLS interno (pi/proxy.py).
- **feat**: aggiunto provider VidXgo (pi/vidxgo.py).
