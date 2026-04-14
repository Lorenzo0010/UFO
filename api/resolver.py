import logging
from typing import Dict
from urllib.parse import quote

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW
from .tmdb import get_tmdb_info, get_episode_title

logger = logging.getLogger(__name__)


def build_easyproxy_url(vixsrc_page_url: str) -> str:
    """
    Costruisce l'URL EasyProxy nel formato:
    EASYPROXY/proxy/hls/manifest.m3u8?d=<encoded_page_url>[&api_password=]
    """
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


async def get_streams(stremio_id: str, content_type: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts = stremio_id.split(":")
        content_id = parts[0]
        season = parts[1] if len(parts) > 1 else None
        episode = parts[2] if len(parts) > 2 else None
        is_series = content_type == "series" and season and episode

        tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
        if not tmdb_id:
            logger.warning(f"\u26a0\ufe0f TMDB ID non trovato per {content_id}")
            return result

        page_url = (
            f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            if is_series
            else f"{SC_DOMAIN}/movie/{tmdb_id}/"
        )
        logger.info(f"\U0001f3ac VixSrc page: {page_url}")

        if not EASYPROXY_URL:
            logger.error("\u274c EASYPROXY_URL non configurato \u2014 nessuno stream possibile")
            return result

        stream_url = build_easyproxy_url(page_url)
        logger.info(f"\u2705 EasyProxy stream: {stream_url[:80]}...")

        # Titolo del contenuto per il campo title dello stream
        if is_series:
            ep_title = await get_episode_title(tmdb_id, season, episode)
            content_label = ep_title or (tmdb_title or "")
        else:
            content_label = tmdb_title or "Film"

        result["streams"].append({
            "name": "UFO\n\U0001f1ee\U0001f1f9",
            "title": content_label,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"\u274c get_streams error: {e}")
    return result
