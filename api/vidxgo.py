"""
vidxgo.py — Provider VidXgo per UFO.

URL pattern (movie):  {VD_DOMAIN}/{imdb_id}
URL pattern (series): {VD_DOMAIN}/{imdb_id}/{season}/{episode}

Note sul 403 / blocco ASN:
  VidXgo risponde 403 alle richieste HEAD provenienti da IP di datacenter
  (VPS, cloud, home server su AS commerciale). Questo NON significa che il
  contenuto sia assente — è un blocco sul check preliminare.
  Il proxy HLS interno di UFO fa la richiesta reale con Referer/Origin
  corretti: se il contenuto non esiste, il proxy riceve 404/403 e il
  client (Stremio) mostra "stream non disponibile" senza crash.
  Pertanto il check HEAD preliminare è stato rimosso: non aggiunge valore
  e causa falsi negativi sistematici da server.

VidXgo firma ogni segmento .ts con token TTL ~5 min (param `e=` epoch ms).
Il proxy HLS interno di UFO è sufficiente perché lavora a livello di
manifest e richiede i segmenti al volo — non è necessario ruotare il
token tra diversi player.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

VD_DOMAIN: str = os.getenv("VIDXGO_DOMAIN", "https://v.vidxgo.co").rstrip("/")
VIDXGO_ENABLED: bool = os.getenv("VIDXGO_ENABLED", "1").lower() not in ("0", "false", "off", "no")


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

    Il proxy:
    - aggiunge Referer: https://v.vidxgo.co/ e Origin: https://v.vidxgo.co
    - legge il manifest M3U8 e riscrive i segmenti .ts attraverso se stesso
    - gestisce 404/403 restituendo errore HTTP al client senza crash
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

    Non esegue alcun check HTTP preliminare: l'URL embed viene passato
    direttamente al proxy HLS interno di UFO che gestisce eventuali errori.

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
        logger.info(f"[VidXgo] skip — IMDB ID mancante o non valido: {imdb_id!r}")
        return None

    is_movie = content_type == "movie"
    embed_url = _build_embed_url(imdb_id, season, episode, is_movie)

    stream_url = _build_proxy_url(embed_url, addon_base_url)
    logger.info(f"[VidXgo] stream via proxy interno: {embed_url}")

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
