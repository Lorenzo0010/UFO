# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc** tramite **EasyProxy (MediaFlow Proxy)**.
> Ottimizzato per il deploy su [Koyeb](https://koyeb.com).

---

## Come funziona

1. Stremio richiede uno stream per un film o una serie (con ID IMDb o TMDB)
2. L'addon risolve l'ID su **TMDB** per ottenere l'ID numerico
3. Costruisce l'URL della pagina **VixSrc** corrispondente
4. Passa quell'URL a **EasyProxy**, che effettua lo scraping e restituisce un manifest `.m3u8` prontamente accessibile
5. Stremio riproduce lo stream HLS direttamente

---

## Struttura del progetto

```
UFO/
├── api/
│   ├── __init__.py
│   ├── index.py       # Entry point FastAPI + route
│   ├── config.py      # Variabili d'ambiente e costanti
│   ├── tmdb.py        # Risoluzione IMDb → TMDB ID
│   └── resolver.py    # Logica stream + costruzione URL EasyProxy
├── Procfile           # Comando di avvio per Koyeb
├── requirements.txt
└── README.md
```

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

MIT
