import asyncio
import logging
import re
from typing import Dict, Optional
from urllib.parse import quote

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW, USER_AGENT
from .tmdb import get_tmdb_info, get_episode_title, get_session

logger = logging.getLogger(__name__)

# Regex per trovare un URL M3U8 nel sorgente HTML o nelle risposte JSON di VixSrc
_M3U8_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*')


def build_easyproxy_url(m3u8_url: str) -> str:
    """
    Costruisce l'URL EasyProxy nel formato:
    EASYPROXY/proxy/hls/manifest.m3u8?d=<encoded_m3u8_url>[&api_password=]
    """
    encoded = quote(m3u8_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


async def extract_m3u8(page_url: str) -> Optional[str]:
    """
    Tenta di estrarre il vero URL M3U8 da una pagina VixSrc.

    Strategia (in ordine):
    1. Chiama l'endpoint /api/source/ di VixSrc (risposta JSON con file[])
    2. Scarica la pagina HTML e cerca un URL .m3u8 nel sorgente
    """
    client = get_session()
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": page_url,
        "Origin": SC_DOMAIN,
    }

    # ── Strategia 1: endpoint /api/source/ ──────────────────────────────────
    # VixSrc espone tipicamente un endpoint che restituisce i file sorgente
    # Es: https://vixsrc.to/api/source/278  oppure  /api/source/tv/2691/6/5
    try:
        # Ricava il path relativo dalla page_url per costruire l'endpoint API
        path = page_url.replace(SC_DOMAIN, "").strip("/")  # es. "movie/278" o "tv/2691/6/5"
        api_url = f"{SC_DOMAIN}/api/source/{path}"
        r = await asyncio.wait_for(
            client.post(api_url, headers=headers, data={"r": SC_DOMAIN, "d": SC_DOMAIN.split("//")[1]}),
            timeout=8.0,
        )
        if r.status_code == 200:
            try:
                data = r.json()
                # Formato atteso: {"success": true, "data": [{"file": "...", "type": "hls"}, ...]}
                sources = data.get("data") or []
                for src in sources:
                    file_url = src.get("file", "")
                    if ".m3u8" in file_url:
                        logger.info(f"✅ M3U8 trovato via API: {file_url[:80]}...")
                        return file_url
            except Exception:
                pass
            # Fallback: cerca .m3u8 nel testo grezzo della risposta
            match = _M3U8_RE.search(r.text)
            if match:
                logger.info(f"✅ M3U8 trovato via API (regex): {match.group()[:80]}...")
                return match.group()
    except Exception as e:
        logger.debug(f"⚠️ API source fallita: {e}")

    # ── Strategia 2: scraping HTML della pagina ──────────────────────────────
    try:
        r = await asyncio.wait_for(
            client.get(page_url, headers=headers),
            timeout=10.0,
        )
        if r.status_code == 200:
            match = _M3U8_RE.search(r.text)
            if match:
                logger.info(f"✅ M3U8 trovato via HTML: {match.group()[:80]}...")
                return match.group()
    except Exception as e:
        logger.debug(f"⚠️ Scraping HTML fallito: {e}")

    logger.warning(f"❌ Nessun M3U8 trovato per {page_url}")
    return None


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
            real_m3u8 = await extract_m3u8(page_url)
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

            real_m3u8 = await extract_m3u8(page_url)
            content_label = tmdb_title or "Film"

        if not real_m3u8:
            logger.error(f"❌ Impossibile estrarre M3U8 per {page_url}")
            return result

        stream_url = build_easyproxy_url(real_m3u8)
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
