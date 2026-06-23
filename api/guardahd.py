"""
guardahd.py — Provider GuardaHD per UFO.

Flusso:
  1. GET {GHD_DOMAIN}/set-movie-a/{imdb_id}
     (solo film — GuardaHD non supporta serie TV)
  2. Parsing HTML: estrae <li data-link="//mixdrop..."> che contengono
     il testo "mixdrop" nel nome
  3. Passa il primo link Mixdrop trovato a resolve_mixdrop()
  4. Restituisce il dict stream Stremio oppure None

Variabili d'ambiente:
  GHD_DOMAIN       default: https://guardahd.stream
  GUARDAHD_ENABLED default: 1  (imposta 0 per disabilitare)
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup, SoupStrainer

from .config import GHD_DOMAIN, GUARDAHD_ENABLED
from .mixdrop import resolve_mixdrop

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0)

_SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Ricerca Mixdrop embed su GuardaHD
# ---------------------------------------------------------------------------

async def _search_mixdrop_embed(imdb_id: str) -> Optional[str]:
    """
    Cerca il link embed Mixdrop per un dato IMDB ID su GuardaHD.
    Restituisce il primo URL mixdrop trovato, oppure None.
    """
    url = f"{GHD_DOMAIN}/set-movie-a/{imdb_id}"
    headers = {
        "User-Agent": _SEARCH_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        "Referer": GHD_DOMAIN + "/",
    }
    logger.info(f"[GuardaHD] 🔍 ricerca embed: {url}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"[GuardaHD] HTTP {resp.status_code} per {url}")
                return None

        soup = BeautifulSoup(resp.text, "lxml", parse_only=SoupStrainer("li"))
        for tag in soup.find_all("li"):
            data_link = tag.get("data-link", "")
            tag_text  = tag.get_text(" ", strip=True).lower()
            if data_link and "mixdrop" in tag_text:
                embed = data_link if data_link.startswith("http") else "https:" + data_link
                logger.info(f"[GuardaHD] ✅ Mixdrop embed trovato: {embed}")
                return embed

        logger.info(f"[GuardaHD] ℹ️  nessun embed Mixdrop per {imdb_id}")
        return None

    except Exception as e:
        logger.warning(f"[GuardaHD] errore ricerca per {imdb_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Funzione pubblica: resolve_guardahd
# ---------------------------------------------------------------------------

async def resolve_guardahd(
    imdb_id: str,
    content_label: str,
    content_type: str,
    addon_base_url: str,
) -> Optional[dict]:
    """
    Resolver GuardaHD → Mixdrop → stream Stremio.

    Args:
        imdb_id:        IMDB ID (es. "tt1234567")
        content_label:  Titolo del film
        content_type:   "movie" | "series" (GuardaHD supporta solo "movie")
        addon_base_url: Base URL del proxy UFO

    Returns:
        dict stream Stremio oppure None.
    """
    enabled = GUARDAHD_ENABLED not in ("0", "false", "off", "no")
    if not enabled:
        logger.debug("[GuardaHD] disabilitato via GUARDAHD_ENABLED")
        return None

    # GuardaHD ha solo film
    if content_type != "movie":
        logger.debug(f"[GuardaHD] skip — tipo '{content_type}' non supportato")
        return None

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.debug(f"[GuardaHD] skip — IMDB ID non valido: {imdb_id}")
        return None

    try:
        embed_url = await _search_mixdrop_embed(imdb_id)
        if not embed_url:
            return None

        return await resolve_mixdrop(
            embed_url=embed_url,
            content_label=content_label,
            source_name="GuardaHD",
            addon_base_url=addon_base_url,
        )

    except Exception as e:
        logger.warning(f"[GuardaHD] ❌ errore inatteso per {imdb_id}: {e}")
        return None
