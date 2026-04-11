import logging
from typing import Optional, Tuple

import httpx

import config as cfg

logger = logging.getLogger(__name__)


async def get_tmdb_id(stremio_id: str, content_type: str) -> Optional[Tuple[int, str]]:
    """
    Converte un ID Stremio (IMDb o TMDB) nel TMDB ID numerico.
    Restituisce (tmdb_id, titolo) oppure None.
    """
    headers = {"User-Agent": cfg.USER_AGENT}

    # Se è già un ID TMDB numerico
    if stremio_id.isdigit():
        try:
            url = f"https://api.themoviedb.org/3/{'tv' if content_type == 'series' else 'movie'}/{stremio_id}"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params={"api_key": cfg.TMDB_API_KEY}, headers=headers)
                r.raise_for_status()
                data = r.json()
                title = data.get("title") or data.get("name") or ""
                return int(stremio_id), title
        except Exception as e:
            logger.error(f"TMDB direct lookup error: {e}")
            return None

    # ID IMDb (tt...)
    if stremio_id.startswith("tt"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.themoviedb.org/3/find/" + stremio_id,
                    params={"api_key": cfg.TMDB_API_KEY, "external_source": "imdb_id"},
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                results = data.get("movie_results") or data.get("tv_results") or []
                if results:
                    item = results[0]
                    title = item.get("title") or item.get("name") or ""
                    return item["id"], title
        except Exception as e:
            logger.error(f"TMDB find error: {e}")
    return None
