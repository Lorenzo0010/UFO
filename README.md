# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc** tramite un sistema a doppio proxy: **EasyProxy** (prioritario) o **MediaFlow Proxy** (fallback con estrazione m3u8 multi-step).
> Supporta il deploy su [Koyeb](https://koyeb.com) e [Vercel](https://vercel.com).

---

## ⚠️ Disclaimer

**Questo progetto è realizzato esclusivamente a scopo educativo e di ricerca.**

L'autore non è responsabile di alcun utilizzo improprio, illegale o non autorizzato del presente software.
Utilizzando questo progetto, l'utente accetta di assumersi la piena responsabilità delle proprie azioni e di rispettare le leggi vigenti nel proprio paese.

- Questo addon **non ospita, non distribuisce e non indicizza** alcun contenuto multimediale
- Funziona esclusivamente come **proxy di reindirizzamento** verso sorgenti di terze parti pubblicamente accessibili
- L'autore **non ha alcun controllo** sui contenuti forniti da sorgenti esterne (VixSrc, EasyProxy, MediaFlow, TMDB)
- L'autore **non garantisce** la disponibilità, la legalità o la qualità dei contenuti raggiungibili tramite questo software
- È responsabilità dell'utente verificare che l'utilizzo di questo software sia conforme alle leggi del proprio paese

> **L'autore declina ogni responsabilità civile e penale derivante dall'uso di questo software.**

---

## 📚 Scopo educativo

Questo progetto nasce come studio pratico dei seguenti argomenti:

- Sviluppo di API REST con **FastAPI** e Python asincrono
- Integrazione con API di terze parti (**TMDB API**)
- Architettura di addon per **Stremio** e il relativo protocollo
- Utilizzo di proxy HLS (**EasyProxy** / **MediaFlow Proxy**) per la gestione di stream
- Estrazione multi-step di manifest `.m3u8` tramite scraping HTML, analisi iframe e API interne
- Deploy su piattaforme cloud moderne (**Koyeb** e **Vercel**)
- Strutturazione di progetti Python in moduli riutilizzabili

Il codice è intenzionalmente documentato e organizzato per essere comprensibile e riutilizzabile come riferimento didattico.

---

## Come funziona

UFO fa da ponte tra Stremio e VixSrc. Il sistema usa un **doppio proxy** per aggirare il blocco degli IP datacenter:

1. **EasyProxy** (prioritario) — se `EASYPROXY_URL` è impostato, viene usato direttamente; riceve l'URL della pagina VixSrc come parametro `?d=` ed effettua internamente scraping + fetch del manifest HLS.
2. **MediaFlow Proxy** (fallback) — se `EASYPROXY_URL` è vuoto ma `MEDIAFLOW_URL` è configurato, UFO estrae autonomamente l'URL `.m3u8` da VixSrc tramite un processo multi-step, e lo passa a MediaFlow per il proxy.

```
Stremio
  │
  │  GET /U0MQ/stream/{type}/{id}.json
  ▼
api/index.py  ──►  api/resolver.py
                        │
                        │  1. Risolve IMDb ID → TMDB ID  (via api/tmdb.py)
                        │  2. Costruisce URL pagina VixSrc
                        │
                        ├──► [EASYPROXY_URL impostato?]
                        │         └── build_easyproxy_url(?d=<vixsrc_page>)
                        │                    ▼
                        │              EasyProxy
                        │                    │  scraping + fetch manifest HLS
                        │                    ▼
                        │             manifest .m3u8 ◄──── Stremio riproduce
                        │
                        └──► [MEDIAFLOW_URL impostato?]
                                  └── extract_m3u8_from_vixsrc() [multi-step]
                                       │  Step 1: cerca .m3u8 nell'HTML diretto
                                       │  Step 2: segue iframe e cerca .m3u8
                                       │  Step 3: chiama API interne VixSrc
                                       ▼
                                  build_mediaflow_url(?d=<m3u8_url>)
                                             ▼
                                       MediaFlow Proxy
                                             │  proxy del manifest HLS
                                             ▼
                                      manifest .m3u8 ◄──── Stremio riproduce
```

### Flusso dettagliato

1. **Stremio** invia una richiesta all'addon con l'ID del contenuto (IMDb `tt…` o TMDB numerico) e il tipo (`movie` / `series`)
2. **`resolver.py`** divide l'ID in parti: `content_id`, `season`, `episode` (per le serie)
3. **`tmdb.py`** chiama l'API TMDB `/find/{imdb_id}` per ottenere l'ID numerico TMDB (se l'ID è già numerico, lo usa direttamente)
4. Viene costruita la **URL della pagina VixSrc** nel formato:
   - Film: `https://vixsrc.to/movie/{tmdb_id}/`
   - Serie: `https://vixsrc.to/tv/{tmdb_id}/{season}/{episode}/`
5. **Priorità 1 — EasyProxy**: se `EASYPROXY_URL` è impostato, la pagina VixSrc viene passata direttamente come `?d=` a EasyProxy, che si occupa di tutto il resto. Lo stream viene restituito immediatamente.
6. **Priorità 2 — MediaFlow**: se solo `MEDIAFLOW_URL` è impostato, UFO estrae l'URL `.m3u8` da VixSrc in modo autonomo tramite tre step progressivi (HTML diretto → iframe → API interne), poi lo passa a MediaFlow per il proxy.

> ⚠️ Almeno uno tra `EASYPROXY_URL` e `MEDIAFLOW_URL` deve essere configurato. Senza proxy, VixSrc blocca le richieste provenienti da IP di datacenter (es. Koyeb, Vercel).

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py       # Rende api/ un package Python (necessario per gli import relativi)
│   ├── index.py          # Entry point: app FastAPI, middleware CORS, tutte le route
│   ├── config.py         # Costanti e lettura variabili d'ambiente (.env / env vars piattaforma)
│   ├── tmdb.py           # Risoluzione IMDb ID → TMDB ID tramite API TMDB
│   └── resolver.py       # Logica principale: estrazione m3u8 multi-step + costruzione URL proxy
├── Procfile              # Comando di avvio per Koyeb: uvicorn api.index:app
├── vercel.json           # Configurazione deploy Vercel (serverless, CORS headers)
├── requirements.txt      # Dipendenze Python: fastapi, httpx, uvicorn, python-dotenv
└── README.md
```

### Descrizione file

#### `api/__init__.py`
File vuoto che trasforma la cartella `api/` in un package Python. Senza di esso gli import relativi (`from .config import ...`) non funzionerebbero.

#### `api/config.py`
Centralizza tutta la configurazione dell'addon. Legge le variabili d'ambiente con `os.getenv()` e fornisce valori di default. Importato da tutti gli altri moduli.

```python
SC_DOMAIN      = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY   = os.getenv("TMDB_KEY", "...")

# EasyProxy (prioritario se impostato)
EASYPROXY_URL  = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW  = os.getenv("EASYPROXY_PASSWORD", "")

# MediaFlow Proxy (fallback se EASYPROXY_URL è vuoto)
MEDIAFLOW_URL  = os.getenv("MEDIAFLOW_URL", "").rstrip("/")
MEDIAFLOW_PSW  = os.getenv("MEDIAFLOW_PASSWORD", "")
```

#### `api/tmdb.py`
Contiene la funzione `get_tmdb_id(content_id, content_type)`. Se l'ID è un IMDb ID (`tt…`), chiama l'endpoint `/find` di TMDB per convertirlo. Gestisce sia film che serie con fallback tra i due tipi.

#### `api/resolver.py`
Cuore logico dell'addon. Contiene:
- `extract_m3u8_from_vixsrc(page_url)` — estrazione multi-step dell'URL `.m3u8` da VixSrc: Step 1 (HTML diretto), Step 2 (iframe), Step 3 (API interne `/api/source/<id>` e `/api/episode/<id>`)
- `build_easyproxy_url(vixsrc_page_url)` — costruisce l'URL EasyProxy passando la pagina VixSrc come `?d=`
- `build_mediaflow_url(m3u8_url)` — costruisce l'URL MediaFlow passando l'URL `.m3u8` estratto come `?d=`
- `get_streams(stremio_id, content_type)` — orchestra la risoluzione TMDB, la scelta del proxy e la generazione dello stream da restituire a Stremio

#### `api/index.py`
Entry point dell'applicazione. Inizializza FastAPI, aggiunge il middleware CORS (necessario per Stremio) e definisce tutte le route. La route `/` espone anche il campo `proxy_mode` che indica quale proxy è attivo (`easyproxy`, `mediaflow` o `none`).

| Route | Funzione |
|---|---|
| `GET /` | Status check + `proxy_mode` + link al manifest |
| `GET /U0MQ/manifest.json` | Manifest Stremio (nome, versione, tipi supportati) |
| `GET /U0MQ/stream/{type}/{id}.json` | **Route principale** — risolve e restituisce gli stream |
| `GET /U0MQ/meta/{type}/{id}.json` | Metadati stub (richiesto dal protocollo Stremio) |
| `GET /U0MQ/catalog/{type}/{id}.json` | Catalogo vuoto (l'addon non fornisce cataloghi) |

#### `Procfile`
Dice a Koyeb come avviare l'app:
```
web: uvicorn api.index:app --host 0.0.0.0 --port $PORT
```

#### `vercel.json`
Configura il deploy serverless su Vercel. Instrada tutte le route verso `api/index.py` e aggiunge gli header CORS necessari per Stremio.

#### `requirements.txt`
| Pacchetto | Utilizzo |
|---|---|
| `fastapi` | Framework web per le API REST |
| `uvicorn` | Server ASGI che esegue FastAPI |
| `httpx` | Client HTTP asincrono per le chiamate a TMDB e per lo scraping VixSrc |
| `python-dotenv` | Caricamento variabili da file `.env` in sviluppo locale |

---

## Prerequisiti

- Almeno **una** delle seguenti istanze proxy raggiungibile pubblicamente:
  - **EasyProxy / MediaFlow Proxy** — usato come proxy HLS principale
    - Repository: [iamrony777/mediaflow-proxy](https://github.com/iamrony777/mediaflow-proxy)
  - **MediaFlow Proxy** — usato come fallback con estrazione `.m3u8` autonoma da parte di UFO
- Una **TMDB API Key** (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api))

---

## Deploy su Koyeb

### 1. Fork o connetti il repo

Connetti questo repository su [Koyeb](https://app.koyeb.com) tramite **GitHub**.

### 2. Configurazione servizio

| Campo | Valore |
|---|---|
| **Builder** | Buildpack |
| **Run command** | `uvicorn api.index:app --host 0.0.0.0 --port $PORT` |
| **Port** | `8000` |

Koyeb legge automaticamente il `Procfile`, quindi il run command è già configurato.

### 3. Variabili d'ambiente

Imposta le seguenti variabili nella sezione **Environment variables** del servizio Koyeb:

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `EASYPROXY_URL` | ⚠️ Almeno uno | URL base della tua istanza EasyProxy (es. `https://myproxy.koyeb.app`) |
| `EASYPROXY_PASSWORD` | ❌ Se configurata | Password dell'istanza EasyProxy |
| `MEDIAFLOW_URL` | ⚠️ Almeno uno | URL base della tua istanza MediaFlow Proxy (usato se `EASYPROXY_URL` è vuoto) |
| `MEDIAFLOW_PASSWORD` | ❌ Se configurata | Password dell'istanza MediaFlow |
| `TMDB_KEY` | ⚠️ Consigliata | La tua API key TMDB personale |
| `SC_DOMAIN` | ❌ No | Dominio VixSrc alternativo (default: `https://vixsrc.to`) |

> Se entrambi `EASYPROXY_URL` e `MEDIAFLOW_URL` sono impostati, viene usato **EasyProxy** come priorità.

### 4. Deploy

Clicca **Deploy**. Koyeb costruirà l'immagine e avvierà il servizio.

---

## Deploy su Vercel

### 1. Fork o connetti il repo

Connetti questo repository su [Vercel](https://vercel.com) tramite **GitHub**.
Il file `vercel.json` è già incluso nel repo e configura automaticamente il routing serverless e gli header CORS.

### 2. Variabili d'ambiente

Imposta le stesse variabili descritte nella sezione Koyeb nella sezione **Environment Variables** del progetto Vercel.

### 3. Deploy

Clicca **Deploy**. Vercel userà `vercel.json` per costruire e instradare tutte le richieste verso `api/index.py`.

---

## Aggiungere l'addon a Stremio

Una volta deployato, copia l'URL del tuo servizio e aprilo nel browser.
La risposta mostrerà il link al manifest e il proxy attivo:

```json
{
  "status": "online",
  "addon": "UFO addon",
  "proxy_mode": "easyproxy",
  "manifest": "https://<tuo-servizio>/U0MQ/manifest.json"
}
```

Incolla quel link manifest in Stremio → **Addon** → **Aggiungi addon tramite URL**.

---

## Sviluppo locale

```bash
# Installa dipendenze
pip install -r requirements.txt

# Crea un file .env
cp .env.example .env  # oppure crealo manualmente

# Avvia il server
uvicorn api.index:app --reload --port 8000
```

**Esempio `.env`:**
```env
# Usa EasyProxy (prioritario)
EASYPROXY_URL=https://myproxy.example.com
EASYPROXY_PASSWORD=mysecretpassword

# Oppure MediaFlow (fallback se EASYPROXY_URL è vuoto)
MEDIAFLOW_URL=https://mymediaflow.example.com
MEDIAFLOW_PASSWORD=mysecretpassword

TMDB_KEY=la_tua_api_key_tmdb
```

---

## Endpoint disponibili

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/` | Status, `proxy_mode` attivo e link al manifest |
| `GET` | `/U0MQ/manifest.json` | Manifest Stremio |
| `GET` | `/U0MQ/stream/{type}/{id}.json` | Risoluzione stream |
| `GET` | `/U0MQ/meta/{type}/{id}.json` | Metadati (stub) |
| `GET` | `/U0MQ/catalog/{type}/{id}.json` | Catalogo (vuoto) |

---

## Licenza

MIT License — vedi file [LICENSE](LICENSE) per i dettagli.

Il software viene fornito **"as is"**, senza garanzie di alcun tipo, esplicite o implicite.
L'autore non è responsabile per danni diretti, indiretti, incidentali o consequenziali derivanti dall'uso di questo software.
