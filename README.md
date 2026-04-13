# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc** tramite **EasyProxy**.
> Supporta il deploy su [Koyeb](https://koyeb.com) e tramite Docker.

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
- Deploy su piattaforma cloud moderna (**Koyeb**) e tramite **Docker**
- Strutturazione di progetti Python in moduli riutilizzabili

Il codice è intenzionalmente documentato e organizzato per essere comprensibile e riutilizzabile come riferimento didattico.

---

## Come funziona

UFO fa da ponte tra Stremio e VixSrc. Il sistema usa **EasyProxy** per aggirare il blocco degli IP datacenter:

```
Stremio
  │
  │  GET /{ADDON_PATH}/stream/{type}/{id}.json
  ▼
api/index.py  ──►  api/resolver.py
                        │
                        │  1. Risolve IMDb ID → TMDB ID  (via api/tmdb.py + cache)
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
3. **`tmdb.py`** controlla la **cache in-memory** — se l'ID è già stato risolto, risponde senza chiamare TMDB; altrimenti chiama l'endpoint `/find` e salva il risultato
4. Viene costruita la **URL della pagina VixSrc** nel formato:
   - Film: `https://vixsrc.to/movie/{tmdb_id}/`
   - Serie: `https://vixsrc.to/tv/{tmdb_id}/{season}/{episode}/`
5. La pagina VixSrc viene passata come `?d=` a **EasyProxy**, che restituisce il manifest HLS

> ⚠️ `EASYPROXY_URL` deve essere configurato. Senza di esso, VixSrc blocca le richieste provenienti da IP di datacenter (es. Koyeb).

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py       # Rende api/ un package Python
│   ├── index.py          # Entry point: app FastAPI, lifespan, route dinamiche via ADDON_PATH
│   ├── config.py         # Env vars, ADDON_PATH, validate_config()
│   ├── tmdb.py           # Risoluzione IMDb → TMDB con cache in-memory e sessione condivisa
│   └── resolver.py       # Costruzione URL EasyProxy e restituzione stream
├── Dockerfile            # Immagine Docker per deploy su VPS/Orange Pi/qualsiasi host
├── Procfile              # Avvio per Koyeb: uvicorn api.index:app --port 8080
├── requirements.txt      # Dipendenze con versioni pinnate
└── README.md
```

### Descrizione file

#### `api/config.py`
Centralizza tutta la configurazione. Le variabili sensibili (`TMDB_KEY`, `EASYPROXY_URL`) non hanno valori di default. `ADDON_PATH` (default `U0MQ`) permette di personalizzare il prefisso delle route senza toccare il codice.

```python
SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "")        # ⚠️ Obbligatoria — impostare via env var
USER_AGENT   = os.getenv("USER_AGENT", "Mozilla/5.0 ...")
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")
ADDON_PATH   = os.getenv("ADDON_PATH", "U0MQ").strip("/")
```

> ⚠️ Non inserire mai `TMDB_KEY` direttamente nel codice.

#### `api/tmdb.py`
Risolve IMDb ID → TMDB ID con **cache in-memory** e **sessione HTTP condivisa** (`AsyncSession` creata una volta sola e riutilizzata). La cache evita chiamate duplicate per lo stesso contenuto durante la sessione.

#### `api/resolver.py`
Costruisce l'URL EasyProxy e restituisce lo stream a Stremio.

#### `api/index.py`
Entry point FastAPI. Il `lifespan` esegue `validate_config()` all'avvio e chiude la sessione HTTP allo shutdown. Le route usano `ADDON_PATH` come prefisso dinamico.

| Route | Funzione |
|---|---|
| `GET /` | Status check + link al manifest |
| `GET /{ADDON_PATH}/manifest.json` | Manifest Stremio |
| `GET /{ADDON_PATH}/stream/{type}/{id}.json` | **Route principale** |
| `GET /{ADDON_PATH}/meta/{type}/{id}.json` | Metadati stub |
| `GET /{ADDON_PATH}/catalog/{type}/{id}.json` | Catalogo vuoto |

#### `Dockerfile`
Immagine basata su `python:3.12-slim`. Copia prima `requirements.txt` per sfruttare la cache layer di Docker, poi il codice sorgente. Porta esposta: `8080`.

#### `Procfile`
```
web: uvicorn api.index:app --host 0.0.0.0 --port 8080
```

#### `requirements.txt`
| Pacchetto | Versione | Utilizzo |
|---|---|---|
| `fastapi` | 0.115.12 | Framework web REST |
| `uvicorn` | 0.34.0 | Server ASGI |
| `curl_cffi` | 0.14.0 | Client HTTP con fingerprint browser |
| `python-dotenv` | 1.1.0 | Caricamento `.env` in locale |
| `beautifulsoup4` | 4.13.4 | Parsing HTML |
| `lxml` | 5.3.1 | Parser XML/HTML |

---

## Prerequisiti

- Un'istanza **EasyProxy** raggiungibile pubblicamente
- Una **TMDB API Key** personale (gratuita su [themoviedb.org](https://www.themoviedb.org/settings/api))
- **Docker** (opzionale, per deploy non-Koyeb)

---

## Deploy su Koyeb

### 1. Connetti il repo

Connetti il repository su [Koyeb](https://app.koyeb.com) tramite **GitHub**.

### 2. Configurazione servizio

| Campo | Valore |
|---|---|
| **Builder** | Buildpack |
| **Run command** | `uvicorn api.index:app --host 0.0.0.0 --port 8080` |
| **Port** | `8080` |

Koyeb legge automaticamente il `Procfile`.

### 3. Variabili d'ambiente

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `TMDB_KEY` | ✅ Sì | API key TMDB personale |
| `EASYPROXY_URL` | ✅ Sì | URL base EasyProxy (es. `https://myproxy.koyeb.app`) |
| `EASYPROXY_PASSWORD` | ❌ Facoltativa | Password EasyProxy |
| `SC_DOMAIN` | ❌ Facoltativa | Dominio VixSrc alternativo (default: `https://vixsrc.to`) |
| `USER_AGENT` | ❌ Facoltativa | User-Agent HTTP personalizzato |
| `ADDON_PATH` | ❌ Facoltativa | Prefisso route (default: `U0MQ`) |

> All'avvio `validate_config()` logga un `⚠️ warning` per ogni variabile obbligatoria mancante.

### 4. Deploy

Clicca **Deploy**. Koyeb avvierà il servizio sulla porta `8080`.

---

## Deploy con Docker

```bash
# Build immagine
docker build -t ufo-addon .

# Avvia il container
docker run -d \
  -p 8080:8080 \
  -e TMDB_KEY=la_tua_api_key \
  -e EASYPROXY_URL=https://myproxy.example.com \
  -e EASYPROXY_PASSWORD=password_opzionale \
  --name ufo \
  ufo-addon
```

Funziona su qualsiasi host con Docker: VPS, Orange Pi, Raspberry Pi, macchina locale.

---

## Aggiungere l'addon a Stremio

Apri nel browser l'URL del servizio. La risposta mostrerà il link al manifest:

```json
{
  "status": "online",
  "addon": "UFO addon",
  "easyproxy": true,
  "manifest": "https://<tuo-servizio>/U0MQ/manifest.json"
}
```

Incolla il link manifest in Stremio → **Addon** → **Aggiungi addon tramite URL**.

---

## Sviluppo locale

```bash
# Installa dipendenze
pip install -r requirements.txt

# Crea il file .env
# (copia l'esempio qui sotto)

# Avvia il server
uvicorn api.index:app --reload --port 8080
```

**Esempio `.env`:**
```env
TMDB_KEY=la_tua_api_key_tmdb
EASYPROXY_URL=https://myproxy.example.com
EASYPROXY_PASSWORD=password_opzionale

# Opzionali
# SC_DOMAIN=https://vixsrc.to
# USER_AGENT=Mozilla/5.0 ...
# ADDON_PATH=U0MQ
```

---

## Endpoint disponibili

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/` | Status e link al manifest |
| `GET` | `/{ADDON_PATH}/manifest.json` | Manifest Stremio |
| `GET` | `/{ADDON_PATH}/stream/{type}/{id}.json` | Risoluzione stream |
| `GET` | `/{ADDON_PATH}/meta/{type}/{id}.json` | Metadati (stub) |
| `GET` | `/{ADDON_PATH}/catalog/{type}/{id}.json` | Catalogo (vuoto) |

---

## Licenza

MIT License — vedi file [LICENSE](LICENSE) per i dettagli.

Il software viene fornito **"as is"**, senza garanzie di alcun tipo.
L'autore non è responsabile per danni derivanti dall'uso di questo software.
