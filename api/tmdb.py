import logging
from typing import Optional

from curl_cffi.requests import AsyncSession

from .config import TMDB_API_KEY

logger = logging.getLogger(__name__)


async def get_tmdb_id(content_id: str, content_type: str) -> Optional[int]:
    """Risolve IMDb ID → TMDB ID. Se l'ID non inizia con 'tt' lo tratta già come TMDB ID."""
    if not content_id.startswith("tt"):
        try:
            return int(content_id)
        except ValueError:
            return None
    try:
        async with AsyncSession() as client:
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
                if data.get(prefer):
                    return data[prefer][0]["id"]
                if data.get(fallback):
                    return data[fallback][0]["id"]
    except Exception as e:
        logger.error(f"❌ TMDb error: {e}")
    return None
