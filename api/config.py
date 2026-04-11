import os

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

# EasyProxy (prioritario se impostato)
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")

# MediaFlow Proxy (fallback se EASYPROXY_URL e' vuoto)
MEDIAFLOW_URL = os.getenv("MEDIAFLOW_URL", "").rstrip("/")
MEDIAFLOW_PSW = os.getenv("MEDIAFLOW_PASSWORD", "")
