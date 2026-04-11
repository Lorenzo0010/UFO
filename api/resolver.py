import logging
from typing import Dict
from urllib.parse import quote

from .config import SC_DOMAIN
from .tmdb import get_tmdb_id

logger = logging.getLogger(__name__)


def build_proxy_url(vixsrc_page_url: str, proxy_url: str, proxy_psw: str) -> str:
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{proxy_url}/proxy/hls/manifest.m3u8?d={encoded}"
    if proxy_psw:
        url += f"&api_password={quote(proxy_psw, safe='')}"
    return url


async def get_streams(stremio_id: str, content_type: str, proxy_url: str, proxy_psw: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts      = stremio_id.split(":")
        content_id = parts[0]
        season     = parts[1] if len(parts) > 1 else None
        episode    = parts[2] if len(parts) > 2 else None
        is_series  = content_type == "series" and season and episode

        tmdb_result = await get_tmdb_id(content_id, content_type)
        if not tmdb_result:
            logger.warning(f"TMDB ID non trovato per {content_id}")
            return result

        tmdb_id, media_title = tmdb_result

        page_url = (
            f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            if is_series
            else f"{SC_DOMAIN}/movie/{tmdb_id}/"
        )
        logger.info(f"VixSrc page: {page_url}")

        stream_url = build_proxy_url(page_url, proxy_url, proxy_psw)

        if media_title:
            stream_title = (
                f"{media_title}\nStagione {season} Episodio {episode}"
                if is_series else media_title
            )
        else:
            stream_title = f"S{season}E{episode}" if is_series else "VixSrc"

        result["streams"].append({
            "name": "VIX",
            "title": stream_title,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"get_streams error: {e}")
    return result
