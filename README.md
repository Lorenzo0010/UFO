# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc** tramite **EasyProxy**.
> Supporta il deploy su [Koyeb](https://koyeb.com).

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
- Utilizzo di **EasyProxy** per la gestione di stream HLS
- Deploy su piattaforma cloud moderna (**Koyeb**)
- Strutturazione di progetti Python in moduli riutilizzabili

Il codice è intenzionalmente documentato e organizzato per essere comprensibile e riutilizzabile come riferimento didattico.

---

## Come funziona

UFO fa da ponte tra Stremio e VixSrc. Il sistema usa **EasyProxy** per aggirare il blocco degli IP datacenter:

1. **EasyProxy** — riceve l'URL della pagina VixSrc come parametro `?d=` ed effettua internamente scraping + fetch del manifest HLS.

```
Stremio
  │
  │  GET /U0MQ/stream/{type}/{id}.json
  ▼
api/index.py  ──►  api/resolver.py
                        │
                        │  1. Risolve IMDb ID → TMDB ID  (via api/tmdb.py)
                        │  2. Costruisce URL pagina VixSrc
                        │  3. Passa la pagina VixSrc a EasyProxy (?d=<vixsrc_page>)
                        ▼
                     EasyProxy
                        │  scraping + fetch manifest HLS
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
5. La pagina VixSrc viene passata direttamente come `?d=` a **EasyProxy**, che si occupa di tutto il resto. Lo stream viene restituito immediatamente.

> ⚠️ `EASYPROXY_URL` deve essere configurato. Senza di esso, VixSrc blocca le richieste provenienti da IP di datacenter (es. Koyeb).

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py       # Rende api/ un package Python (necessario per gli import relativi)
│   ├── index.py          # Entry point: app FastAPI, middleware CORS, lifespan, tutte le route
│   ├── config.py         # Costanti, lettura env vars e validazione configurazione all'avvio
│   ├── tmdb.py           # Risoluzione IMDb ID → TMDB ID tramite API TMDB
│   └── resolver.py       # Logica principale: costruzione URL EasyProxy e restituzione stream
├── Procfile              # Comando di avvio per Koyeb: uvicorn api.index:app --port 8080
├── requirements.txt      # Dipendenze Python: fastapi, uvicorn, curl_cffi, python-dotenv, ecc.
└── README.md
```

### Descrizione file

#### `api/__init__.py`
File vuoto che trasforma la cartella `api/` in un package Python. Senza di esso gli import relativi (`from .config import ...`) non funzionerebbero.

#### `api/config.py`
Centralizza tutta la configurazione dell'addon. Legge le variabili d'ambiente con `os.getenv()` senza valori di default per le chiavi sensibili. All'avvio viene chiamata `validate_config()` che logga un warning per ogni variabile obbligatoria mancante.

```python
SC_DOMAIN     = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY  = os.getenv("TMDB_KEY", "")        # ⚠️ Nessun valore di default — impostare via env var
USER_AGENT    = os.getenv("USER_AGENT", "Mozilla/5.0 ...")

EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")
```

> ⚠️ Non inserire mai la TMDB API key direttamente nel codice. Usare sempre la variabile d'ambiente `TMDB_KEY`.

#### `api/tmdb.py`
Contiene la funzione `get_tmdb_id(content_id, content_type)`. Se l'ID è un IMDb ID (`tt…`), chiama l'endpoint `/find` di TMDB per convertirlo. Gestisce sia film che serie con fallback tra i due tipi.

#### `api/resolver.py`
Cuore logico dell'addon. Contiene:
- `build_easyproxy_url(vixsrc_page_url)` — costruisce l'URL EasyProxy passando la pagina VixSrc come `?d=`
- `get_streams(stremio_id, content_type)` — orchestra la risoluzione TMDB e la generazione dello stream da restituire a Stremio

#### `api/index.py`
Entry point dell'applicazione. Inizializza FastAPI con un `lifespan` che esegue `validate_config()` all'avvio, aggiunge il middleware CORS (necessario per Stremio) e definisce tutte le route. Le eccezioni nei route vengono loggate con `logger.exception()` per preservare il traceback completo.

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
web: uvicorn api.index:app --host 0.0.0.0 --port 8080
```

#### `requirements.txt`
| Pacchetto | Utilizzo |
|---|---|
| `fastapi` | Framework web per le API REST |
| `uvicorn` | Server ASGI che esegue FastAPI |
| `curl_cffi` | Client HTTP con fingerprint browser per le chiamate a TMDB e VixSrc |
| `python-dotenv` | Caricamento variabili da file `.env` in sviluppo locale |
| `beautifulsoup4` | Parsing HTML |
| `lxml` | Parser XML/HTML per BeautifulSoup |

---

## Prerequisiti

- Un'istanza **EasyProxy** raggiungibile pubblicamente
- Una **TMDB API Key** personale (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api))

---

## Deploy su Koyeb

### 1. Fork o connetti il repo

Connetti questo repository su [Koyeb](https://app.koyeb.com) tramite **GitHub**.

### 2. Configurazione servizio

| Campo | Valore |
|---|---|
| **Builder** | Buildpack |
| **Run command** | `uvicorn api.index:app --host 0.0.0.0 --port 8080` |
| **Port** | `8080` |

Koyeb legge automaticamente il `Procfile`, quindi il run command è già configurato.

### 3. Variabili d'ambiente

Imposta le seguenti variabili nella sezione **Environment variables** del servizio Koyeb:

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `TMDB_KEY` | ✅ Sì | La tua API key TMDB personale — **non hardcodarla mai nel codice** |
| `EASYPROXY_URL` | ✅ Sì | URL base della tua istanza EasyProxy (es. `https://myproxy.koyeb.app`) |
| `EASYPROXY_PASSWORD` | ❌ Se configurata | Password dell'istanza EasyProxy |
| `SC_DOMAIN` | ❌ No | Dominio VixSrc alternativo (default: `https://vixsrc.to`) |
| `USER_AGENT` | ❌ No | User-Agent HTTP personalizzato |

> All'avvio, `validate_config()` logga automaticamente un `⚠️ warning` per ogni variabile obbligatoria mancante.

### 4. Deploy

Clicca **Deploy**. Koyeb costruirà l'immagine e avvierà il servizio sulla porta `8080`.

---

## Aggiungere l'addon a Stremio

Una volta deployato, copia l'URL del tuo servizio e aprilo nel browser.
La risposta mostrerà il link al manifest:

```json
{
  "status": "online",
  "addon": "UFO addon",
  "easyproxy": true,
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
uvicorn api.index:app --reload --port 8080
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
