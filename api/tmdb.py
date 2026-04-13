import logging
from typing import Optional

from curl_cffi.requests import AsyncSession

from .config import TMDB_API_KEY, USER_AGENT

logger = logging.getLogger(__name__)

# Sessione HTTP condivisa — creata una volta sola, riutilizzata per tutte le chiamate
_session: Optional[AsyncSession] = None

# Cache in-memory: chiave "(content_id, content_type)" → TMDB ID
_tmdb_cache: dict[tuple[str, str], Optional[int]] = {}


def get_session() -> AsyncSession:
    """Restituisce la sessione HTTP condivisa, creandola se non esiste ancora."""
    global _session
    if _session is None:
        _session = AsyncSession(headers={"User-Agent": USER_AGENT})
    return _session


async def close_session() -> None:
    """Chiude la sessione HTTP condivisa. Da chiamare allo shutdown dell'app."""
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def get_tmdb_id(content_id: str, content_type: str) -> Optional[int]:
    """Risolve IMDb ID → TMDB ID con cache in-memory.
    Se l'ID non inizia con 'tt' lo tratta già come TMDB ID.
    """
    # ID già numerico: nessuna chiamata necessaria
    if not content_id.startswith("tt"):
        try:
            return int(content_id)
        except ValueError:
            return None

    cache_key = (content_id, content_type)

    # Cache hit
    if cache_key in _tmdb_cache:
        logger.debug(f"🗃️  Cache hit TMDB per {content_id} ({content_type})")
        return _tmdb_cache[cache_key]

    # Cache miss — chiama l'API TMDB
    result: Optional[int] = None
    try:
        client = get_session()
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
                result = data[prefer][0]["id"]
            elif data.get(fallback):
                result = data[fallback][0]["id"]
    except Exception as e:
        logger.error(f"❌ TMDb error: {e}")

    _tmdb_cache[cache_key] = result
    logger.debug(f"💾 Cache miss TMDB — salvato {content_id} → {result}")
    return result
