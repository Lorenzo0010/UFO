import os
import logging

logger = logging.getLogger(__name__)

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "")
USER_AGENT   = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")

# EasyProxy (opzionale)
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")

# MediaFlow Proxy (opzionale, ha priorità su EasyProxy se configurato)
MEDIAFLOW_URL = os.getenv("MEDIAFLOW_URL", "").rstrip("/")
MEDIAFLOW_PSW = os.getenv("MEDIAFLOW_PASSWORD", "")


def validate_config() -> None:
    """Verifica che le variabili obbligatorie siano presenti all'avvio."""
    missing = []
    if not TMDB_API_KEY:
        missing.append("TMDB_KEY")

    if missing:
        for var in missing:
            logger.warning(f"⚠️  Variabile d'ambiente mancante: {var}")
    else:
        logger.info("✅ Configurazione validata con successo")

    if MEDIAFLOW_URL:
        logger.info(f"🔀 Modalità proxy: MediaFlow ({MEDIAFLOW_URL})")
    elif EASYPROXY_URL:
        logger.info(f"🔀 Modalità proxy: EasyProxy ({EASYPROXY_URL})")
    else:
        logger.info("📡 Modalità proxy: Direct streaming (nessun proxy configurato)")
