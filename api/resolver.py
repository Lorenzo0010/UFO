import asyncio
import logging
from typing import Dict
from urllib.parse import quote

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW
from .tmdb import get_tmdb_info, get_episode_title, get_session

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


async def warmup_proxy(url: str) -> None:
    """Tocca l'URL del proxy in background per avviare l'estrazione anticipata.
    Non blocca la risposta — viene lanciato come task asincrono."""
    try:
        client = get_session()
        await asyncio.wait_for(client.head(url), timeout=2.0)
        logger.debug("🔥 Proxy warm-up inviato")
    except Exception:
        pass  # intenzionalmente silenzioso — è solo un suggerimento


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

            if not EASYPROXY_URL:
                logger.error("❌ EASYPROXY_URL non configurato — nessuno stream possibile")
                return result

            ep_title_task = asyncio.create_task(get_episode_title(tmdb_id, season, episode))
            stream_url = build_easyproxy_url(page_url)
            asyncio.create_task(warmup_proxy(stream_url))
            ep_title = await ep_title_task
            content_label = ep_title or tmdb_title or ""
        else:
            tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
                return result

            page_url = f"{SC_DOMAIN}/movie/{tmdb_id}/"
            logger.info(f"🎬 VixSrc page: {page_url}")

            if not EASYPROXY_URL:
                logger.error("❌ EASYPROXY_URL non configurato — nessuno stream possibile")
                return result

            stream_url = build_easyproxy_url(page_url)
            asyncio.create_task(warmup_proxy(stream_url))
            content_label = tmdb_title or "Film"

        logger.info(f"✅ EasyProxy stream: {stream_url[:80]}...")

        result["streams"].append({
            "name": "UFO\n🇮🇹",
            "title": content_label,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": True,
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
