import os

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Proxy integrato: se EASYPROXY_URL è vuoto, il proxy è self-hosted (auto-detect in index.py)
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")

# Base URL del server (auto-popolato da index.py alla prima richiesta)
_BASE_URL: str = ""


def get_proxy_base() -> str:
    """Ritorna la base URL da usare per costruire gli URL proxy."""
    return EASYPROXY_URL if EASYPROXY_URL else _BASE_URL


def set_base_url(url: str):
    global _BASE_URL
    if not _BASE_URL:
        _BASE_URL = url
