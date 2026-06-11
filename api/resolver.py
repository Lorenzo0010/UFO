import asyncio
import logging
from typing import Dict
from urllib.parse import quote

from .config import (
    SC_DOMAIN,
    EASYPROXY_URL, EASYPROXY_PSW,
    MEDIAFLOW_URL, MEDIAFLOW_PSW,
)
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


def build_mediaflow_url(vixsrc_page_url: str) -> str:
    """
    Costruisce l'URL MediaFlow Proxy nel formato:
    MEDIAFLOW/proxy/hls/manifest.m3u8?d=<encoded_page_url>[&api_password=]
    """
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{MEDIAFLOW_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if MEDIAFLOW_PSW:
        url += f"&api_password={quote(MEDIAFLOW_PSW, safe='')}"
    return url


def build_stream_url(vixsrc_page_url: str) -> tuple[str, str]:
    """
    Sceglie il proxy da usare in base alle variabili d'ambiente:
    1. MediaFlow (se MEDIAFLOW_URL è configurato)
    2. EasyProxy (se EASYPROXY_URL è configurato)
    3. Direct (nessun proxy)
    Ritorna (stream_url, label_proxy)
    """
    if MEDIAFLOW_URL:
        return build_mediaflow_url(vixsrc_page_url), "MediaFlow"
    elif EASYPROXY_URL:
        return build_easyproxy_url(vixsrc_page_url), "EasyProxy"
    else:
        return vixsrc_page_url, "Direct"


async def get_streams(stremio_id: str, content_type: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts = stremio_id.split(":")
        content_id = parts[0]
        season = parts[1] if len(parts) > 1 else None
        episode = parts[2] if len(parts) > 2 else None
        is_series = content_type == "series" and season and episode

        if is_series:
            tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
                return result

            page_url = f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            logger.info(f"🎬 VixSrc page: {page_url}")

            ep_title_task = asyncio.create_task(get_episode_title(tmdb_id, season, episode))
            stream_url, proxy_label = build_stream_url(page_url)
            ep_title = await ep_title_task
            content_label = ep_title or tmdb_title or ""
        else:
            tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
                return result

            page_url = f"{SC_DOMAIN}/movie/{tmdb_id}/"
            logger.info(f"🎬 VixSrc page: {page_url}")

            stream_url, proxy_label = build_stream_url(page_url)
            content_label = tmdb_title or "Film"

        logger.info(f"✅ [{proxy_label}] stream: {stream_url[:80]}...")

        result["streams"].append({
            "name": "UFO\n🇮🇹",
            "title": content_label,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
