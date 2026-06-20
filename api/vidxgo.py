"""
vidxgo.py — Provider VidXgo per UFO.

URL pattern (movie):  {VD_DOMAIN}/{imdb_id}
URL pattern (series): {VD_DOMAIN}/{imdb_id}/{season}/{episode}

Perché serve EasyProxy:
  VidXgo firma ogni segmento .ts con un token TTL ~5 minuti (param `e=` epoch ms).
  Un proxy HLS passivo (come proxy.py di UFO) legge il manifest una volta e
  forwarda i segmenti: dopo ~5 min il token scade e la riproduzione si interrompe.
  EasyProxy ha un loop interno che rinnova il token in background e riscrive
  i segmenti al volo — stessa architettura usata da StreamVix.

  Se EASYPROXY_URL non è configurata, lo stream viene comunque proposto
  tramite il proxy HLS interno di UFO (funzionerà per film brevi o
  visualizzazioni < 5 min, poi il player mostra errore).
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

VD_DOMAIN: str = os.getenv("VIDXGO_DOMAIN", "https://v.vidxgo.co").rstrip("/")
VIDXGO_ENABLED: bool = os.getenv("VIDXGO_ENABLED", "1").lower() not in ("0", "false", "off", "no")

# EasyProxy — token rotation per VidXgo
EASYPROXY_URL: str = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW: str = os.getenv("EASYPROXY_PASSWORD", "")


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _build_embed_url(imdb_id: str, season: Optional[str], episode: Optional[str], is_movie: bool) -> str:
    """Costruisce l'URL embed VidXgo (usa IMDB id, NON tmdb)."""
    clean = imdb_id.split(":")[0]
    if is_movie or not season or not episode:
        return f"{VD_DOMAIN}/{clean}"
    return f"{VD_DOMAIN}/{clean}/{season}/{episode}"


def _build_ep_url(embed_url: str) -> str:
    """
    Wrappa l'URL embed in EasyProxy.
    EP esegue l'estrazione, cattura il manifest e avvia il loop di rinnovo token.
    Endpoint: {EP_BASE}/proxy/hls/manifest.m3u8?d=<embed_url>[&api_password=<psw>]
    """
    params: dict = {"d": embed_url}
    if EASYPROXY_PSW:
        params["api_password"] = EASYPROXY_PSW
    return f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?{urlencode(params)}"


def _build_internal_proxy_url(embed_url: str, addon_base_url: str) -> str:
    """
    Fallback: proxy HLS interno di UFO.
    Funziona ma senza token rotation — riproduzione limitata a ~5 min.
    """
    base = addon_base_url.rstrip("/")
    encoded = quote(embed_url, safe="")
    referer = quote(f"{VD_DOMAIN}/", safe="")
    return f"{base}/proxy/manifest.m3u8?url={encoded}&referer={referer}"


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

async def resolve_vidxgo(
    imdb_id: str,
    content_label: str,
    content_type: str,
    season: Optional[str],
    episode: Optional[str],
    addon_base_url: str,
) -> Optional[Dict]:
    """
    Restituisce un dict stream Stremio oppure None se VidXgo non può essere usato.

    Priorità proxy:
      1. EasyProxy (EASYPROXY_URL impostata) — token rotation, riproduzione completa
      2. Proxy HLS interno UFO              — no rotation, ~5 min poi errore
    """
    if not VIDXGO_ENABLED:
        logger.debug("[VidXgo] disabilitato (VIDXGO_ENABLED=0)")
        return None

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.info(f"[VidXgo] skip — IMDB ID mancante o non valido: {imdb_id!r}")
        return None

    is_movie = content_type == "movie"
    embed_url = _build_embed_url(imdb_id, season, episode, is_movie)

    if EASYPROXY_URL:
        stream_url = _build_ep_url(embed_url)
        proxy_label = f"EasyProxy ({EASYPROXY_URL})"
    else:
        stream_url = _build_internal_proxy_url(embed_url, addon_base_url)
        proxy_label = "proxy interno UFO (no token rotation — imposta EASYPROXY_URL)"

    logger.info(f"[VidXgo] embed: {embed_url} — proxy: {proxy_label}")

    binge_group = "ufo-vidxgo-movie" if is_movie else f"ufo-vidxgo-s{season}e{episode}"
    return {
        "name": "UFO\n🌍 VidXgo",
        "title": content_label,
        "url": stream_url,
        "behaviorHints": {
            "notWebReady": True,
            "bingeGroup": binge_group,
        },
    }
