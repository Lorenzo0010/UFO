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

## 🚀 Come funziona e Doppio Funzionamento

UFO aggrega stream da tre provider in parallelo ed è stato progettato con un **doppio funzionamento** per due piattaforme distinte:

### 1. Modalità Addon Stremio (Server-Side)
Il backend in Python orchestrato da FastAPI esegue lo scraping e il fetch dai provider direttamente dal server. 
Utilizza un **proxy HLS interno** che riscrive i manifest (m3u8) e inoltra i segmenti video per bypassare limitazioni CORS e blocchi regionali tipici dei browser e dei player TV.

### 2. Modalità Plugin Nuvio (Client-Side)
Il codice Javascript contenuto in `src/` viene analizzato e pacchettizzato tramite esbuild (grazie a `build.js`) per generare moduli browser-compatibili. Questi script vengono caricati ed eseguiti **direttamente dal client Nuvio** dell'utente (Smart TV, Smartphone, PC).
Le richieste HTTP vengono effettuate nativamente tramite l'API `fetch` del dispositivo. Per questo motivo, solo i provider con supporto nativo CORS e senza blocchi Cloudflare troppo restrittivi sono attivati su Nuvio (es. VixCloud, VidXgo, AltadefinizioneStreaming).

---

## 📥 Installazione e Utilizzo

### 🛸 Per Stremio
Poiché la modalità Stremio richiede un server proxy per bypassare i CORS, devi eseguire l'addon su un server (o in locale):
1. Fai il deploy del codice su un servizio di hosting (es. Koyeb, Render, VPS con Docker).
2. Apri l'URL della tua istanza nel browser (es. `https://mio-ufo-server.koyeb.app`).
3. Clicca sul pulsante **"Install on Stremio"** o copia il link e incollalo nella barra di ricerca degli Addon di Stremio.

### 🎬 Per Nuvio
Nuvio esegue il plugin direttamente nel client, quindi non serve un server:
1. Copia l'URL raw del file `manifest.json` di questo repository (o della tua fork hostata su GitHub Pages/Raw).
2. Apri Nuvio, vai nella sezione **Plugin / Estensioni**.
3. Aggiungi il plugin incollando l'URL del manifest.

---

## 📂 Struttura del progetto

```text
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
```

---

## 💻 Sviluppo

### Sviluppo Plugin Nuvio (Client-Side)

Assicurati di avere Node.js installato.

```bash
npm install
npm run build
```
Questo comando analizzerà il codice in `src/` e genererà la cartella `providers/` contenente i file JS compilati e pronti per essere letti da Nuvio.

**GitHub Actions**: Il repository è configurato per compilare automaticamente i provider ad ogni push sul branch `main`. Nuvio andrà a leggere direttamente i file compilati.

### Sviluppo Addon Stremio (Server-Side)

```bash
# Installa le dipendenze Python
pip install -r requirements.txt

# Crea il file .env per le variabili d'ambiente
cp .env.example .env

# Avvia il server di sviluppo (FastAPI)
uvicorn api.index:app --reload --port 8080
```

---

## 📋 Changelog

### [2.0.0] — 2026-07
> Dual-Support Nuvio e Pulizia Provider

- **feat**: Aggiunto supporto nativo al plugin **Nuvio**, con esecuzione client-side nel browser.
- **feat**: Pipeline esbuild (`build.js`) e `package.json` integrati per raggruppare i file JS.
- **feat**: Aggiunta la CI/CD via GitHub Actions per pacchettizzare il codice su ogni push.
- **refactor**: Eliminati i provider bloccati da Cloudflare o non compatibili nativamente (Guardoserie, AnimeUnity, AnimeSaturn, NetMirror, ecc.).
- **refactor**: Mantenuti e stabilizzati i tre provider autonomi primari: **VixCloud, VidXgo e AltadefinizioneStreaming**.
- **fix**: Sostituiti moduli Node.js-only (`axios`, `fs`, `https`) con un fallback sicuro nativo (`fetch`) per il client browser.
- **chore**: Riordinata la lista dei provider nel `manifest.json`.

### [1.7.0] — 2026-06
> Rimozione EasyProxy, Fix Proxy e Porting

- **refactor**: rimossa completamente la dipendenza da EasyProxy per Stremio.
- **fix**: aggiornato Dockerfile per esporre la porta 8080.

### [1.6.0] — 2026-06
> Proxy HLS interno e VidXgo

- **feat**: proxy HLS interno (`api/proxy.py`).
- **feat**: aggiunto provider VidXgo (`api/vidxgo.py`).
