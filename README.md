# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc/VixCloud** e **VidXgo** tramite un **proxy HLS interno**.
> Supporta il deploy su [Koyeb](https://koyeb.com) e tramite Docker.

---

## ⚠️ Disclaimer

**Questo progetto è realizzato esclusivamente a scopo educativo e di ricerca.**

L'autore non è responsabile di alcun utilizzo improprio, illegale o non autorizzato del presente software.
Utilizzando questo progetto, l'utente accetta di assumersi la piena responsabilità delle proprie azioni e di rispettare le leggi vigenti nel proprio paese.

- Questo addon **non ospita, non distribuisce e non indicizza** alcun contenuto multimediale
- Funziona esclusivamente come **proxy di reindirizzamento** verso sorgenti di terze parti pubblicamente accessibili
- L'autore **non ha alcun controllo** sui contenuti forniti da sorgenti esterne (VixSrc, VidXgo, TMDB)
- L'autore **non garantisce** la disponibilità, la legalità o la qualità dei contenuti raggiungibili tramite questo software
- È responsabilità dell'utente verificare che l'utilizzo di questo software sia conforme alle leggi del proprio paese

> **L'autore declina ogni responsabilità civile e penale derivante dall'uso di questo software.**

---

## 📚 Scopo educativo

Questo progetto nasce come studio pratico dei seguenti argomenti:

- Sviluppo di API REST con **FastAPI** e Python asincrono
- Integrazione con API di terze parti (**TMDB API**)
- Architettura di addon per **Stremio** e il relativo protocollo
- Proxy HLS interno (riscrittura manifest `.m3u8` e inoltro segmenti)
- Deploy su piattaforma cloud moderna (**Koyeb**) e tramite **Docker**
- Strutturazione di progetti Python in moduli riutilizzabili

---

## Come funziona

UFO aggrega stream da due provider in parallelo tramite un **proxy HLS interno** che riscrive i manifest e inoltra i segmenti video.

```
Stremio
  │
  │  GET /stream/{type}/{id}.json
  ▼
api/index.py  ──►  api/resolver.py
                        │
                        │  asyncio.gather() ──────────────────────────────────┐
                        │                                                      │
                        ├── 1. VixSrc/VixCloud ─────────────────────────────┐ │
                        │      a. Risolve IMDb → TMDB  (tmdb.py + cache)    │ │
                        │      b. Chiama /api/movie|tv/<tmdb>                │ │
                        │      c. Estrae URL embed VixCloud                  │ │
                        │      d. Estrae token/expires/m3u8 dallo script     │ │
                        │      e. HEAD check disponibilità (opzionale)       │ │
                        │      f. Proxy HLS interno → Stremio                │ │
                        │                                                     │ │
                        └── 2. VidXgo ─────────────────────────────────────  │ │
                               Richiede EASYPROXY_URL per funzionare          │ │
                               a. Usa IMDb ID diretto (no TMDB)              │ │
                               b. Costruisce {VIDXGO_DOMAIN}/{imdb}[/s/e]    │ │
                               c. Passa a EasyProxy per token rotation       │ │
                                  (fallback: proxy interno, ~5 min poi errore)│ │
                                                                               │ │
                   ┌───────────────────────────────────────────────────────────┘ │
                   │                                                              │
                   └──────────────────────────────────────────────────────────────┘
                   ▼
           api/proxy.py  ──►  /proxy/manifest.m3u8?url=<encoded>
                               Riscrive URI nel manifest e inoltra segmenti
                               Stremio riproduce
```

### Proxy HLS interno

Il proxy (`api/proxy.py`) agisce da intermediario tra Stremio e le sorgenti HLS:

1. `GET /proxy/manifest.m3u8?url=<encoded>` — scarica il manifest e riscrive tutti gli URI (segmenti, chiavi, sotto-playlist) come URL proxy
2. `GET /proxy/segment?url=<encoded>` — rileva automaticamente se la risposta è un sotto-manifesto (anche senza `.m3u8` nell'URL) e lo riscrive, altrimenti inoltra il segmento direttamente
3. Gli header originali (Referer, User-Agent, ecc.) vengono propagati in ogni richiesta tramite `headers_b64`

> Per ambienti multi-client (es. Stremio desktop + mobile sullo stesso server), impostare **`ADDON_BASE_URL`** con l'URL pubblico del servizio; altrimenti viene usato `request.base_url` come fallback (funziona solo per client con lo stesso IP).

### Perché VidXgo richiede EasyProxy

VidXgo firma ogni segmento `.ts` con un token con TTL di ~5 minuti (parametro `e=` epoch ms). Il proxy HLS interno di UFO è **passivo**: legge il manifest una volta e inoltra i segmenti, ma non può rinnovare il token. Dopo ~5 minuti il token scade e la riproduzione si interrompe.

EasyProxy invece ha un **loop interno di rinnovo token** che riscrive i segmenti al volo, garantendo la riproduzione completa. Senza `EASYPROXY_URL`, VidXgo viene comunque proposto come stream ma la riproduzione si interrompe dopo pochi minuti.

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py       # Rende api/ un package Python
│   ├── index.py          # Entry point: app FastAPI, lifespan, route
│   ├── config.py         # Env vars e validate_config()
│   ├── tmdb.py           # Risoluzione IMDb → TMDB con cache in-memory e sessione condivisa
│   ├── resolver.py       # Orchestrazione provider (VixSrc/VixCloud, VidXgo)
│   ├── vidxgo.py         # Provider VidXgo (richiede EasyProxy per riproduzione completa)
│   └── proxy.py          # Proxy HLS interno (manifest + segmenti)
├── Dockerfile            # Immagine Docker per deploy su VPS/Orange Pi/qualsiasi host
├── Procfile              # Avvio per Koyeb: uvicorn api.index:app --port 8000
├── requirements.txt      # Dipendenze con versioni pinnate
└── README.md
```

### Descrizione moduli

#### `api/config.py`
Centralizza tutta la configurazione. Le variabili sensibili (`TMDB_KEY`) non hanno valori di default. `validate_config()` viene chiamata al lifespan di FastAPI e logga warning per ogni variabile obbligatoria mancante, oltre a info sullo stato delle variabili opzionali.

#### `api/tmdb.py`
Risolve IMDb ID → TMDB ID con **cache in-memory** e **sessione HTTP condivisa** (`AsyncSession` creata una volta sola e riutilizzata). La cache evita chiamate duplicate per lo stesso contenuto durante la sessione.

#### `api/resolver.py`
Orchestratore principale. Lancia i due provider in parallelo con `asyncio.gather()` e aggrega tutti gli stream validi nel risultato restituito a Stremio.

#### `api/vidxgo.py`
Provider VidXgo. Usa l'IMDb ID direttamente (non richiede TMDB). **Richiede `EASYPROXY_URL`** per la riproduzione completa: VidXgo firma ogni segmento con un token TTL ~5 min che solo EasyProxy rinnova automaticamente. Senza EasyProxy viene usato il proxy interno come fallback, ma la riproduzione si interrompe dopo ~5 minuti.

#### `api/proxy.py`
Proxy HLS interno. Espone due route (`/proxy/manifest.m3u8` e `/proxy/segment`) e riscrive ogni URI nei manifest per passare per il proxy stesso, propagando gli header originali.

#### `api/index.py`
Entry point FastAPI. Il `lifespan` esegue `validate_config()` all'avvio e chiude le sessioni HTTP allo shutdown.

| Route | Funzione |
|---|---|
| `GET /` | Status check + link al manifest |
| `GET /manifest.json` | Manifest Stremio |
| `GET /stream/{type}/{id}.json` | **Route principale** |
| `GET /meta/{type}/{id}.json` | Metadati stub |
| `GET /catalog/{type}/{id}.json` | Catalogo vuoto |
| `GET /proxy/manifest.m3u8` | Proxy HLS — manifest |
| `GET /proxy/segment` | Proxy HLS — segmenti |

#### `Dockerfile`
Immagine basata su `python:3.12-slim`. Copia prima `requirements.txt` per sfruttare la cache layer di Docker, poi il codice sorgente. Porta esposta: `8000`.

#### `Procfile`
```
web: uvicorn api.index:app --host 0.0.0.0 --port 8000
```

#### `requirements.txt`
| Pacchetto | Versione | Utilizzo |
|---|---|---|
| `fastapi` | 0.115.12 | Framework web REST |
| `uvicorn` | 0.34.0 | Server ASGI |
| `httpx` | — | Client HTTP asincrono |
| `curl_cffi` | 0.14.0 | Client HTTP con fingerprint browser |
| `python-dotenv` | 1.1.0 | Caricamento `.env` in locale |
| `beautifulsoup4` | 4.13.4 | Parsing HTML |
| `lxml` | 5.3.1 | Parser XML/HTML |

---

## 🔧 Variabili d'ambiente

### Obbligatorie

| Variabile | Descrizione |
|---|---|
| `TMDB_KEY` | API key TMDB personale (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api)) |

### Consigliate

| Variabile | Default | Descrizione |
|---|---|---|
| `ADDON_BASE_URL` | *(vuoto)* | URL pubblico fisso del servizio (es. `https://mio-addon.koyeb.app`). **Necessario** per ambienti multi-client (Stremio desktop + mobile sullo stesso server). Se non impostato, viene usato `request.base_url` come fallback (funziona solo se tutti i client hanno lo stesso IP). |
| `EASYPROXY_URL` | *(vuoto)* | URL base EasyProxy (es. `https://myproxy.koyeb.app`). **Necessario per VidXgo**: senza di esso la riproduzione VidXgo si interrompe dopo ~5 minuti per scadenza token. |

### Provider

| Variabile | Default | Descrizione |
|---|---|---|
| `SC_DOMAIN` | `https://vixsrc.to` | Dominio VixSrc alternativo |
| `VIDXGO_DOMAIN` | `https://v.vidxgo.co` | Dominio VidXgo alternativo |
| `VIDXGO_ENABLED` | `1` | Abilita il provider VidXgo. Impostare `0` per disabilitarlo |
| `VIXSRC_SKIP_LIST_CHECK` | *(vuoto)* | Se `1`, salta il controllo HEAD sulla disponibilità del contenuto VixSrc. Utile se VixSrc blocca le richieste HEAD dall'IP del server |

### EasyProxy

EasyProxy gestisce il **rinnovo automatico dei token** per VidXgo. Ogni segmento `.ts` di VidXgo ha un TTL di ~5 minuti: il proxy interno di UFO è passivo e non può rinnovarli, mentre EasyProxy ha un loop dedicato che li aggiorna al volo.

| Variabile | Default | Descrizione |
|---|---|---|
| `EASYPROXY_URL` | *(vuoto)* | URL base EasyProxy. Necessario per la riproduzione completa di VidXgo |
| `EASYPROXY_PASSWORD` | *(vuoto)* | Password EasyProxy (se configurata) |

### HTTP

| Variabile | Default | Descrizione |
|---|---|---|
| `USER_AGENT` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0)…` | User-Agent HTTP personalizzato per le richieste verso i provider |

---

## Prerequisiti

- Una **TMDB API Key** personale (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api))
- **Docker** (opzionale, per deploy non-Koyeb)
- Un'istanza **EasyProxy** raggiungibile pubblicamente (necessaria per VidXgo con riproduzione completa)

---

## Deploy su Koyeb

### 1. Connetti il repo

Connetti il repository su [Koyeb](https://app.koyeb.com) tramite **GitHub**.

### 2. Configurazione servizio

| Campo | Valore |
|---|---|
| **Builder** | Buildpack |
| **Run command** | `uvicorn api.index:app --host 0.0.0.0 --port 8000` |
| **Port** | `8000` |

Koyeb legge automaticamente il `Procfile`.

### 3. Variabili d'ambiente

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `TMDB_KEY` | ✅ Sì | API key TMDB personale |
| `ADDON_BASE_URL` | ⚠️ Consigliata | URL pubblico del servizio Koyeb (es. `https://mio-addon.koyeb.app`) |
| `EASYPROXY_URL` | ⚠️ Consigliata | URL EasyProxy — necessario per VidXgo (riproduzione > 5 min) |
| `EASYPROXY_PASSWORD` | ❌ No | Password EasyProxy |
| `VIDXGO_ENABLED` | ❌ No | `0` per disabilitare VidXgo (default: abilitato) |
| `VIXSRC_SKIP_LIST_CHECK` | ❌ No | `1` per saltare il controllo HEAD di VixSrc |
| `SC_DOMAIN` | ❌ No | Dominio VixSrc alternativo |
| `USER_AGENT` | ❌ No | User-Agent HTTP personalizzato |

### 4. Deploy

Clicca **Deploy**. Koyeb avvierà il servizio sulla porta `8000`.

---

## Deploy con Docker

```bash
# Build immagine
docker build -t ufo-addon .

# Avvia il container
docker run -d \
  -p 8000:8000 \
  -e TMDB_KEY=la_tua_api_key \
  -e ADDON_BASE_URL=http://192.168.1.77:8000 \
  -e EASYPROXY_URL=https://myproxy.example.com \
  --name ufo \
  ufo-addon
```

Funziona su qualsiasi host con Docker: VPS, Orange Pi, Raspberry Pi, macchina locale.

**Con tutte le opzioni:**

```bash
docker run -d \
  -p 8000:8000 \
  -e TMDB_KEY=la_tua_api_key \
  -e ADDON_BASE_URL=http://192.168.1.77:8000 \
  -e EASYPROXY_URL=https://myproxy.example.com \
  -e EASYPROXY_PASSWORD=password_opzionale \
  -e VIDXGO_ENABLED=1 \
  -e VIXSRC_SKIP_LIST_CHECK=0 \
  --name ufo \
  ufo-addon
```

---

## Aggiungere l'addon a Stremio

Apri nel browser l'URL del servizio. La risposta mostrerà il link al manifest:

```json
{
  "status": "online",
  "addon": "UFO addon",
  "proxy": "internal",
  "manifest": "https://<tuo-servizio>/manifest.json"
}
```

Incolla il link manifest in Stremio → **Addon** → **Aggiungi addon tramite URL**.

---

## Sviluppo locale

```bash
# Installa dipendenze
pip install -r requirements.txt

# Crea il file .env
cp .env.example .env  # oppure crea manualmente

# Avvia il server
uvicorn api.index:app --reload --port 8000
```

**Esempio `.env`:**
```env
TMDB_KEY=la_tua_api_key_tmdb
ADDON_BASE_URL=http://localhost:8000

# EasyProxy — necessario per VidXgo (token rotation, riproduzione completa)
# Senza questo, VidXgo si interrompe dopo ~5 minuti
EASYPROXY_URL=https://myproxy.example.com
# EASYPROXY_PASSWORD=

# Provider (opzionali — tutti abilitati di default)
# VIDXGO_ENABLED=1
# VIXSRC_SKIP_LIST_CHECK=0

# Avanzate
# SC_DOMAIN=https://vixsrc.to
# VIDXGO_DOMAIN=https://v.vidxgo.co
# USER_AGENT=Mozilla/5.0 ...
```

---

## Endpoint disponibili

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/` | Status e link al manifest |
| `GET` | `/manifest.json` | Manifest Stremio |
| `GET` | `/stream/{type}/{id}.json` | Risoluzione stream (tutti i provider) |
| `GET` | `/meta/{type}/{id}.json` | Metadati (stub) |
| `GET` | `/catalog/{type}/{id}.json` | Catalogo (vuoto) |
| `GET` | `/proxy/manifest.m3u8` | Proxy HLS — manifest |
| `GET` | `/proxy/segment` | Proxy HLS — segmenti |

---

## Licenza

MIT License — vedi file [LICENSE](LICENSE) per i dettagli.

Il software viene fornito **"as is"**, senza garanzie di alcun tipo.
L'autore non è responsabile per danni derivanti dall'uso di questo software.

---

## 📋 Changelog

### [1.6.0] — 2026-06
> Proxy HLS interno e VidXgo

- **feat**: proxy HLS interno (`api/proxy.py`) — riscrive manifest e inoltra segmenti; EasyProxy non è più necessario per VixSrc
- **feat**: aggiunto provider **VidXgo** (`api/vidxgo.py`) — usa IMDb ID diretto; richiede `EASYPROXY_URL` per la riproduzione completa (token TTL ~5 min)
- **feat**: entrambi i provider vengono eseguiti in parallelo con `asyncio.gather()`; tutti gli stream validi vengono restituiti insieme
- **feat**: aggiunta variabile `ADDON_BASE_URL` per ambienti multi-client
- **feat**: aggiunta variabile `VIXSRC_SKIP_LIST_CHECK` per saltare il controllo HEAD di VixSrc
- **fix**: `EASYPROXY_URL` mantenuta per VidXgo, non più usata da VixSrc

### [1.5.0] — 2026-04-13
> Rimozione ADDON_PATH

- **refactor**: rimossa la variabile d'ambiente `ADDON_PATH` — le route sono ora servite direttamente alla radice (`/manifest.json`, `/stream/...`, ecc.)
- Su Koyeb ogni deploy ha già il proprio dominio dedicato, quindi il prefisso dinamico era ridondante
- **bump**: versione manifest aggiornata a `1.5.0`

### [1.4.1] — 2026-04-13
> Porta aggiornata a 8000 per compatibilità Koyeb

- **fix**: porta aggiornata da `8080` a `8000` in README, `Procfile`, `Dockerfile` e negli esempi Docker/sviluppo locale

### [1.4.0] — 2026-04-13
> Sicurezza, performance e infrastruttura

- **security**: rimosso il valore di fallback hardcodato della TMDB API key — la variabile `TMDB_KEY` è ora obbligatoria
- **perf**: aggiunta cache in-memory per le risoluzioni IMDb → TMDB; aggiunta sessione HTTP condivisa
- **chore**: dipendenze pinnate in `requirements.txt`; aggiunto `Dockerfile`
