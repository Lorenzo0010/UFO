"""
vidxgo.py — Provider VidXgo per UFO.

URL pattern (movie):  {VD_DOMAIN}/{imdb_id}
URL pattern (series): {VD_DOMAIN}/{imdb_id}/{season}/{episode}

VidXgo firma ogni segmento .ts con token TTL ~5 min (param `e=` epoch ms).
Il proxy HLS interno di UFO è sufficiente perché lavora a livello di manifest
e richiede il segmento al volo con gli header corretti — non è necessario
ruotare il token tra diversi player (a differenza di EasyProxy nel contesto
StreamVix). Se il token dovesse scadere durante la riproduzione, il client
richiede un nuovo manifest e il ciclo ricomincia.

Dipendenze: httpx (già usato da resolver.py)
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

VD_DOMAIN: str = os.getenv("VIDXGO_DOMAIN", "https://v.vidxgo.co").rstrip("/")
VIDXGO_ENABLED: bool = os.getenv("VIDXGO_ENABLED", "1").lower() not in ("0", "false", "off", "no")

_TIMEOUT = httpx.Timeout(15.0)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _build_embed_url(imdb_id: str, season: Optional[str], episode: Optional[str], is_movie: bool) -> str:
    """Costruisce l'URL embed VidXgo (usa IMDB id, NON tmdb)."""
    clean = imdb_id.split(":")[0]
    if is_movie or not season or not episode:
        return f"{VD_DOMAIN}/{clean}"
    return f"{VD_DOMAIN}/{clean}/{season}/{episode}"


def _build_proxy_url(embed_url: str, addon_base_url: str) -> str:
    """
    Avvolge l'URL embed VidXgo nel proxy HLS interno di UFO.
    Il proxy legge il manifest, riscrive i segmenti e aggiunge gli header
    corretti (Referer/Origin) a ogni richiesta verso il CDN VidXgo.
    """
    base = addon_base_url.rstrip("/")
    encoded = quote(embed_url, safe="")
    return f"{base}/proxy/manifest.m3u8?url={encoded}"


async def _check_embed_reachable(embed_url: str) -> bool:
    """Verifica HEAD sull'URL VidXgo (skip con VIDXGO_SKIP_CHECK=1)."""
    if os.getenv("VIDXGO_SKIP_CHECK", "").lower() in ("1", "true", "on", "yes"):
        return True
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.head(embed_url, headers={"User-Agent": _UA})
        if resp.status_code in range(200, 400):
            return True
        if resp.status_code == 404:
            logger.info(f"[VidXgo] 404 -> {embed_url}")
            return False
        logger.warning(f"[VidXgo] HEAD {resp.status_code} su {embed_url} — assumo raggiungibile")
        return True
    except Exception as e:
        logger.warning(f"[VidXgo] HEAD error per {embed_url}: {e} — assumo raggiungibile")
        return True


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
    Restituisce un dict stream Stremio oppure None se VidXgo non è disponibile.

    Parametri:
      imdb_id        ID IMDB (es. "tt1234567") — già risolto da get_tmdb_info
      content_label  Titolo da mostrare in Stremio
      content_type   "movie" | "series"
      season / episode  numero stagione/episodio (stringa) o None
      addon_base_url  base URL dell'addon per il proxy HLS interno
    """
    if not VIDXGO_ENABLED:
        logger.debug("[VidXgo] disabilitato (VIDXGO_ENABLED=0)")
        return None

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.debug(f"[VidXgo] IMDB ID mancante o non valido: {imdb_id!r}")
        return None

    is_movie = content_type == "movie"
    embed_url = _build_embed_url(imdb_id, season, episode, is_movie)
    logger.info(f"[VidXgo] embed URL: {embed_url}")

    reachable = await _check_embed_reachable(embed_url)
    if not reachable:
        logger.warning(f"[VidXgo] contenuto non trovato: {embed_url}")
        return None

    stream_url = _build_proxy_url(embed_url, addon_base_url)
    logger.info(f"[VidXgo] stream proxy: {stream_url[:100]}...")

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
