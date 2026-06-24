import os
import logging

logger = logging.getLogger(__name__)

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://images.seeklogo.com/logo-png/14/2/ufo-plast-logo-png_seeklogo-144349.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "")
USER_AGENT   = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")

# VidXgo provider
VIDXGO_DOMAIN  = os.getenv("VIDXGO_DOMAIN", "https://v.vidxgo.co")
VIDXGO_ENABLED = os.getenv("VIDXGO_ENABLED", "1")   # imposta 0 per disabilitare

# Base URL fisso per il proxy interno — OBBLIGATORIO se si usa
# qualsiasi client che accede da un IP diverso da quello che ha fatto la
# richiesta /stream. Es: http://192.168.1.77:7000
# Se non impostato, si usa request.base_url come fallback (single-client only).
ADDON_BASE_URL = os.getenv("ADDON_BASE_URL", "").rstrip("/")


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

    if ADDON_BASE_URL:
        logger.info(f"ℹ️  ADDON_BASE_URL (fisso): {ADDON_BASE_URL}")
    else:
        logger.warning("⚠️  ADDON_BASE_URL non impostata — uso request.base_url (potrebbe non funzionare con multi-client)")

    logger.info(f"ℹ️  VidXgo:   {'abilitato' if VIDXGO_ENABLED not in ('0','false','off','no') else 'disabilitato'} ({VIDXGO_DOMAIN})")

