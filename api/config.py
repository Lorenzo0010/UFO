import os

ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

# Variabili per il proxy MediaFlow — da impostare su Vercel
PROXY_URL    = os.getenv("PROXY_URL", "")   # es. https://mio-proxy.vercel.app
PROXY_PSW    = os.getenv("PROXY_PSW", "")   # password proxy (opzionale)
