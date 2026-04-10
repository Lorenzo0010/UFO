# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc** tramite **EasyProxy (MediaFlow Proxy)**.
> Ottimizzato per il deploy su [Koyeb](https://koyeb.com).

---

## ⚠️ Disclaimer

**Questo progetto è realizzato esclusivamente a scopo educativo e di ricerca.**

L'autore non è responsabile di alcun utilizzo improprio, illegale o non autorizzato del presente software.
Utilizzando questo progetto, l'utente accetta di assumersi la piena responsabilità delle proprie azioni e di rispettare le leggi vigenti nel proprio paese.

- Questo addon **non ospita, non distribuisce e non indicizza** alcun contenuto multimediale
- Funziona esclusivamente come **proxy di reindirizzamento** verso sorgenti di terze parti pubblicamente accessibili
- L'autore **non ha alcun controllo** sui contenuti forniti da sorgenti esterne (VixSrc, EasyProxy, TMDB)
- L'autore **non garantisce** la disponibilità, la legalità o la qualità dei contenuti raggiungibili tramite questo software
- È responsabilità dell'utente verificare che l'utilizzo di questo software sia conforme alle leggi del proprio paese

> **L'autore declina ogni responsabilità civile e penale derivante dall'uso di questo software.**

---

## 📚 Scopo educativo

Questo progetto nasce come studio pratico dei seguenti argomenti:

- Sviluppo di API REST con **FastAPI** e Python asincrono
- Integrazione con API di terze parti (**TMDB API**)
- Architettura di addon per **Stremio** e il relativo protocollo
- Utilizzo di proxy HLS (**MediaFlow Proxy / EasyProxy**) per la gestione di stream
- Deploy su piattaforme cloud moderne (**Koyeb**)
- Strutturazione di progetti Python in moduli riutilizzabili

Il codice è intenzionalmente documentato e organizzato per essere comprensibile e riutilizzabile come riferimento didattico.

---

## Come funziona

UFO fa da ponte tra Stremio e VixSrc, aggirando il blocco degli IP datacenter grazie a EasyProxy.

```
Stremio
  │
  │  GET /U0MQ/stream/{type}/{id}.json
  ▼
api/index.py  ──►  api/resolver.py
                        │
                        │  1. Risolve IMDb ID → TMDB ID  (via api/tmdb.py)
                        │  2. Costruisce URL pagina VixSrc
                        │  3. Genera URL EasyProxy con ?d=<vixsrc_page>
                        │
                        ▼
                   EasyProxy (MediaFlow Proxy)
                        │  scraping pagina VixSrc + fetch manifest HLS
                        ▼
                   manifest .m3u8  ◄──── Stremio riproduce lo stream
```

### Flusso dettagliato

1. **Stremio** invia una richiesta all'addon con l'ID del contenuto (IMDb `tt…` o TMDB numerico) e il tipo (`movie` / `series`)
2. **`resolver.py`** divide l'ID in parti: `content_id`, `season`, `episode` (per le serie)
3. **`tmdb.py`** chiama l'API TMDB `/find/{imdb_id}` per ottenere l'ID numerico TMDB (se l'ID è già numerico, lo usa direttamente)
4. Viene costruita la **URL della pagina VixSrc** nel formato:
   - Film: `https://vixsrc.to/movie/{tmdb_id}/`
   - Serie: `https://vixsrc.to/tv/{tmdb_id}/{season}/{episode}/`
5. Quella URL viene passata come parametro `?d=` a **EasyProxy**, che effettua lo scraping della pagina e restituisce un manifest `.m3u8` proxy-ato
6. Lo stream viene restituito a Stremio come oggetto con `url`, `name` e `behaviorHints`

> ⚠️ EasyProxy è **indispensabile**: VixSrc blocca le richieste dirette provenienti da IP di datacenter (come quelli di Koyeb).

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py       # Rende api/ un package Python (necessario per gli import relativi)
│   ├── index.py          # Entry point: app FastAPI, middleware CORS, tutte le route
│   ├── config.py         # Costanti e lettura variabili d'ambiente (.env / Koyeb env vars)
│   ├── tmdb.py           # Risoluzione IMDb ID → TMDB ID tramite API TMDB
│   └── resolver.py       # Logica principale: costruzione URL VixSrc + URL EasyProxy
├── Procfile              # Comando di avvio letto da Koyeb: uvicorn api.index:app
├── requirements.txt      # Dipendenze Python: fastapi, httpx, uvicorn, python-dotenv
└── README.md
```

### Descrizione file

#### `api/__init__.py`
File vuoto che trasforma la cartella `api/` in un package Python. Senza di esso gli import relativi (`from .config import ...`) non funzionerebbero.

#### `api/config.py`
Centralizza tutta la configurazione dell'addon. Legge le variabili d'ambiente con `os.getenv()` e fornisce valori di default. Importato da tutti gli altri moduli.

```python
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "")     # URL istanza EasyProxy
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "") # Password EasyProxy
TMDB_API_KEY  = os.getenv("TMDB_KEY", "...")        # API key TMDB
SC_DOMAIN     = os.getenv("SC_DOMAIN", "https://vixsrc.to")
```

#### `api/tmdb.py`
Contiene la funzione `get_tmdb_id(content_id, content_type)`. Se l'ID è un IMDb ID (`tt…`), chiama l'endpoint `/find` di TMDB per convertirlo. Gestisce sia film che serie con fallback tra i due tipi.

#### `api/resolver.py`
Cuore logico dell'addon. Contiene:
- `build_easyproxy_url(vixsrc_page_url)` — codifica l'URL VixSrc e costruisce l'URL EasyProxy con parametro `?d=` e `api_password` opzionale
- `get_streams(stremio_id, content_type)` — orchestra la risoluzione TMDB, la costruzione dell'URL VixSrc e la generazione dello stream da restituire a Stremio

#### `api/index.py`
Entry point dell'applicazione. Inizializza FastAPI, aggiunge il middleware CORS (necessario per Stremio) e definisce tutte le route:

| Route | Funzione |
|---|---|
| `GET /` | Status check + link al manifest |
| `GET /U0MQ/manifest.json` | Manifest Stremio (nome, versione, tipi supportati) |
| `GET /U0MQ/stream/{type}/{id}.json` | **Route principale** — risolve e restituisce gli stream |
| `GET /U0MQ/meta/{type}/{id}.json` | Metadati stub (richiesto dal protocollo Stremio) |
| `GET /U0MQ/catalog/{type}/{id}.json` | Catalogo vuoto (l'addon non fornisce cataloghi) |

#### `Procfile`
Dice a Koyeb come avviare l'app:
```
web: uvicorn api.index:app --host 0.0.0.0 --port $PORT
```

#### `requirements.txt`
| Pacchetto | Utilizzo |
|---|---|
| `fastapi` | Framework web per le API REST |
| `uvicorn` | Server ASGI che esegue FastAPI |
| `httpx` | Client HTTP asincrono per le chiamate a TMDB |
| `python-dotenv` | Caricamento variabili da file `.env` in sviluppo locale |

---

## Prerequisiti

- Un'istanza **EasyProxy / MediaFlow Proxy** raggiungibile pubblicamente
  - Repository: [iamrony777/mediaflow-proxy](https://github.com/iamrony777/mediaflow-proxy)
  - Necessario perché VixSrc blocca le richieste da IP datacenter
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
| `EASYPROXY_URL` | ✅ Sì | URL base della tua istanza EasyProxy (es. `https://myproxy.koyeb.app`) |
| `EASYPROXY_PASSWORD` | ⚠️ Se configurata | Password dell'istanza EasyProxy |
| `TMDB_KEY` | ⚠️ Consigliata | La tua API key TMDB personale |
| `SC_DOMAIN` | ❌ No | Dominio VixSrc alternativo (default: `https://vixsrc.to`) |

### 4. Deploy

Clicca **Deploy**. Koyeb costruirà l'immagine e avvierà il servizio.

---

## Aggiungere l'addon a Stremio

Una volta deployato, copia l'URL del tuo servizio Koyeb e aprilo nel browser.
La risposta mostrerà il link al manifest:

```json
{
  "manifest": "https://<tuo-servizio>.koyeb.app/U0MQ/manifest.json"
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
EASYPROXY_URL=https://myproxy.example.com
EASYPROXY_PASSWORD=mysecretpassword
TMDB_KEY=la_tua_api_key_tmdb
```

---

## Endpoint disponibili

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/` | Status e link al manifest |
| `GET` | `/U0MQ/manifest.json` | Manifest Stremio |
| `GET` | `/U0MQ/stream/{type}/{id}.json` | Risoluzione stream |
| `GET` | `/U0MQ/meta/{type}/{id}.json` | Metadati (stub) |
| `GET` | `/U0MQ/catalog/{type}/{id}.json` | Catalogo (vuoto) |

---

## Licenza

MIT License — vedi file [LICENSE](LICENSE) per i dettagli.

Il software viene fornito **"as is"**, senza garanzie di alcun tipo, esplicite o implicite.
L'autore non è responsabile per danni diretti, indiretti, incidentali o consequenziali derivanti dall'uso di questo software.
