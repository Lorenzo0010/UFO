import os
import logging

logger = logging.getLogger(__name__)

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "")
USER_AGENT   = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")

# EasyProxy rimosso — il proxy HLS è ora interno a UFO
# Mantenuto per retrocompatibilità ma non più usato
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")

# ── IPTV Live TV ─────────────────────────────────────────────────────────────
# Lista di URL M3U/M3U8 da usare come sorgenti canali live.
# Puoi aggiungere ulteriori URL separati da virgola nella variabile d'ambiente
# IPTV_URLS oppure modificare la lista DEFAULT_IPTV_URLS qui sotto.
DEFAULT_IPTV_URLS = [
    "https://raw.githubusercontent.com/maginetweb-arch/TVITALIA/refs/heads/main/iptvit.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/refs/heads/master/playlists/playlist_italy.m3u8",
]

_env_iptv = os.getenv("IPTV_URLS", "")
IPTV_URLS: list[str] = (
    [u.strip() for u in _env_iptv.split(",") if u.strip()]
    if _env_iptv
    else DEFAULT_IPTV_URLS
)

# Numero massimo di canali per pagina nel catalogo
IPTV_PAGE_SIZE = int(os.getenv("IPTV_PAGE_SIZE", "100"))


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

    logger.info(f"📺 Sorgenti IPTV configurate: {len(IPTV_URLS)}")
    for u in IPTV_URLS:
        logger.info(f"   → {u}")
