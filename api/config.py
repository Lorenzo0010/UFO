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

# EasyProxy rimosso — il proxy HLS è ora interno a UFO
# Mantenuto per retrocompatibilità ma non più usato
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")


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

    if EASYPROXY_URL:
        logger.info("ℹ️  EASYPROXY_URL impostata ma non usata — il proxy HLS è ora interno")

    logger.info(f"ℹ️  VidXgo: {'abilitato' if VIDXGO_ENABLED not in ('0','false','off','no') else 'disabilitato'} ({VIDXGO_DOMAIN})")
