# 🛸 UFO — Stremio Addon

> Addon Stremio che fornisce stream HLS da **VixSrc/VixCloud** e **VidXgo**.
> Supporta il deploy su Docker, CasaOS, Koyeb, Render o VPS.

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
- Deploy su piattaforme cloud moderne e orchestrazione con **Docker** e **CasaOS**
- Strutturazione di un proxy HLS interno per flussi video

---

## 🚀 Come funziona

UFO aggrega stream dai provider in parallelo.
Il backend in Python orchestrato da FastAPI esegue lo scraping e il fetch dai provider direttamente dal server. 
Utilizza un **proxy HLS interno** che riscrive i manifest (m3u8) e inoltra i segmenti video per bypassare limitazioni CORS e blocchi regionali tipici dei browser e dei player TV.

---

## ⚙️ Variabili d'ambiente

L'addon si configura tramite variabili d'ambiente (visibili nel file `.env.example`):

| Variabile | Obbligatoria | Descrizione |
| :--- | :---: | :--- |
| `TMDB_KEY` | ✅ **Sì** | Chiave API di TheMovieDB, necessaria per risolvere ID e metadati. |
| `ADDON_BASE_URL` | ⚠️ *Consigliata* | URL pubblico/LAN dell'addon (es. `http://192.168.1.77:8080`). **Obbligatoria** se usi l'addon su dispositivi diversi dallo stesso server. |
| `VIDXGO_ENABLED` | No | Abilita (`1`) o disabilita (`0`) il provider VidXgo. Default: `1`. |
| `SC_DOMAIN` | No | Dominio personalizzato per VixSrc. Default: `https://vixsrc.to`. |
| `VIDXGO_DOMAIN` | No | Dominio personalizzato per VidXgo. Default: `https://v.vidxgo.co`. |

---

## 📥 Installazione e Deploy

L'addon richiede un server per bypassare i CORS e riscrivere i flussi video. Una volta installato, apri l'URL della tua istanza nel browser e clicca su **"Install on Stremio"**.

### 🐳 CasaOS / ZimaBoard
Il progetto include il supporto nativo per CasaOS.
Se hai CasaOS, puoi installare l'addon importando il file `docker-compose.yml` direttamente dall'App Store personalizzato o tramite l'interfaccia di installazione custom, le icone e i metadati verranno rilevati in automatico.

### 🐳 Docker Compose (VPS / Server locale)
```bash
docker compose up -d
```

### ☁️ Cloud (Koyeb, Render)
Puoi fare il deploy diretto tramite Dockerfile o usando il `Procfile` incluso per le piattaforme compatibili. Assicurati di impostare la variabile `TMDB_KEY` nelle impostazioni del servizio cloud.

---

## 💻 Sviluppo in locale

```bash
# Clona il repository e posizionati nella cartella
git clone <url-repo> && cd UFO

# Installa le dipendenze Python
pip install -r requirements.txt

# Crea il file .env partendo dall'esempio
cp .env.example .env

# Modifica il file .env inserendo la tua TMDB_KEY

# Avvia il server di sviluppo (FastAPI)
uvicorn api.index:app --reload --port 8080
```

---

## 📂 Struttura del progetto

```text
UFO/
├── api/                  # Backend Python per Stremio (FastAPI)
├── Dockerfile            # Immagine Docker per il deploy
├── docker-compose.yml    # Configurazione per Docker e CasaOS
├── Procfile              # Avvio per Koyeb
├── requirements.txt      # Dipendenze Python
├── .env.example          # Template per variabili d'ambiente
└── README.md
```
