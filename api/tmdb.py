import logging
from typing import Optional, Tuple

import httpx

from .config import TMDB_API_KEY

logger = logging.getLogger(__name__)


async def get_tmdb_id(content_id: str, content_type: str) -> Optional[Tuple[int, str]]:
    """Risolve IMDb ID → (TMDB ID, titolo). Se l'ID non inizia con 'tt' lo tratta già come TMDB ID (titolo vuoto)."""
    if not content_id.startswith("tt"):
        try:
            return int(content_id), ""
        except ValueError:
            return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}",
                params={
                    "external_source": "imdb_id",
                    "api_key": TMDB_API_KEY,
                    "language": "it",
                },
            )
            if r.status_code == 200:
                data = r.json()
                prefer   = "tv_results"    if content_type == "series" else "movie_results"
                fallback = "movie_results" if content_type == "series" else "tv_results"
                for key in (prefer, fallback):
                    if data.get(key):
                        item  = data[key][0]
                        tmdb_id = item["id"]
                        title   = item.get("name") or item.get("title") or ""
                        return tmdb_id, title
    except Exception as e:
        logger.error(f"❌ TMDb error: {e}")
    return None
