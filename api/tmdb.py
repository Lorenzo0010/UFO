import logging
from typing import Optional, Tuple

from curl_cffi.requests import AsyncSession

from .config import TMDB_API_KEY, USER_AGENT

logger = logging.getLogger(__name__)

# Sessione HTTP condivisa — creata una volta sola, riutilizzata per tutte le chiamate
_session: Optional[AsyncSession] = None

# Cache in-memory: chiave "(content_id, content_type)" → (TMDB ID, titolo)
_tmdb_cache: dict[tuple[str, str], Optional[Tuple[int, str]]] = {}

# Cache episodi: chiave "(tmdb_id, season, episode)" → titolo episodio
_episode_cache: dict[tuple[int, str, str], Optional[str]] = {}


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


async def get_tmdb_info(content_id: str, content_type: str) -> Tuple[Optional[int], Optional[str]]:
    """Risolve IMDb ID → (TMDB ID, titolo) con cache in-memory.
    Se l'ID non inizia con 'tt' lo tratta già come TMDB ID (titolo None).
    """
    if not content_id.startswith("tt"):
        try:
            return int(content_id), None
        except ValueError:
            return None, None

    cache_key = (content_id, content_type)

    if cache_key in _tmdb_cache:
        logger.debug(f"\U0001f5c3\ufe0f  Cache hit TMDB per {content_id} ({content_type})")
        cached = _tmdb_cache[cache_key]
        if cached is None:
            return None, None
        return cached

    result: Optional[Tuple[int, str]] = None
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
            entry = None
            if data.get(prefer):
                entry = data[prefer][0]
            elif data.get(fallback):
                entry = data[fallback][0]
            if entry:
                tmdb_id = entry["id"]
                title = entry.get("title") or entry.get("name") or ""
                result = (tmdb_id, title)
    except Exception as e:
        logger.error(f"\u274c TMDb error: {e}")

    _tmdb_cache[cache_key] = result
    logger.debug(f"\U0001f4be Cache miss TMDB — salvato {content_id} \u2192 {result}")
    if result is None:
        return None, None
    return result


async def get_episode_title(tmdb_id: int, season: str, episode: str) -> Optional[str]:
    """Recupera il titolo di un episodio specifico da TMDB con cache in-memory."""
    cache_key = (tmdb_id, season, episode)
    if cache_key in _episode_cache:
        return _episode_cache[cache_key]

    title: Optional[str] = None
    try:
        client = get_session()
        r = await client.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}",
            params={"api_key": TMDB_API_KEY, "language": "it"},
        )
        if r.status_code == 200:
            data = r.json()
            title = data.get("name") or None
    except Exception as e:
        logger.error(f"\u274c TMDb episode error: {e}")

    _episode_cache[cache_key] = title
    return title


async def get_tmdb_id(content_id: str, content_type: str) -> Optional[int]:
    """Compatibilità: restituisce solo il TMDB ID."""
    tmdb_id, _ = await get_tmdb_info(content_id, content_type)
    return tmdb_id
