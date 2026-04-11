import re
import logging
from typing import Dict, Optional
from urllib.parse import quote

import httpx

from .config import (
    SC_DOMAIN, USER_AGENT,
    MEDIAFLOW_URL, MEDIAFLOW_PSW,
    EASYPROXY_URL, EASYPROXY_PSW,
)
from .tmdb import get_tmdb_id

logger = logging.getLogger(__name__)

# Headers che simulano un browser per VixSrc
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": SC_DOMAIN,
}

# Regex per trovare l'URL .m3u8 nell'HTML/JS di VixSrc
_M3U8_RE = re.compile(r'["\']([^"\']+ \.m3u8[^"\']* )["\']', re.VERBOSE)
_SOURCE_RE = re.compile(r'source\s*:\s*["\']([^"\']+ \.m3u8[^"\']* )["\']', re.VERBOSE)
_FILE_RE = re.compile(r'file\s*:\s*["\']([^"\']+ \.m3u8[^"\']* )["\']', re.VERBOSE)


async def extract_m3u8_from_vixsrc(page_url: str) -> Optional[str]:
    """
    Fa una richiesta HTTP alla pagina VixSrc e cerca l'URL .m3u8
    nell'HTML o negli script inline.
    Restituisce il primo URL .m3u8 trovato, oppure None.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(page_url, headers=BROWSER_HEADERS)
            resp.raise_for_status()
            html = resp.text

        # Prova prima i pattern specifici (source/file), poi il generico
        for pattern in (_SOURCE_RE, _FILE_RE, _M3U8_RE):
            m = pattern.search(html)
            if m:
                url = m.group(1).strip()
                logger.info(f"✅ m3u8 estratto da VixSrc: {url[:80]}...")
                return url

        # Fallback: cerca qualsiasi stringa contenente .m3u8
        m = re.search(r'https?://[^"\' <>]+\.m3u8[^"\' <>]*', html)
        if m:
            url = m.group(0)
            logger.info(f"✅ m3u8 trovato (fallback): {url[:80]}...")
            return url

        logger.warning(f"⚠️ Nessun .m3u8 trovato nella pagina: {page_url}")
        return None

    except Exception as e:
        logger.error(f"❌ Errore estrazione m3u8 da VixSrc: {e}")
        return None


def build_mediaflow_url(m3u8_url: str) -> str:
    """
    Costruisce l'URL MediaFlow Proxy nel formato:
    MEDIAFLOW_URL/proxy/hls/manifest.m3u8?d=<encoded_m3u8_url>[&api_password=<pw>]
    """
    encoded = quote(m3u8_url, safe="")
    url = f"{MEDIAFLOW_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if MEDIAFLOW_PSW:
        url += f"&api_password={quote(MEDIAFLOW_PSW, safe='')}"
    return url


def build_easyproxy_url(vixsrc_page_url: str) -> str:
    """
    Costruisce l'URL EasyProxy (legacy) nel formato:
    EASYPROXY_URL/proxy/hls/manifest.m3u8?d=<encoded_vixsrc_page>[&api_password=<pw>]
    """
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


async def get_streams(stremio_id: str, content_type: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts      = stremio_id.split(":")
        content_id = parts[0]
        season     = parts[1] if len(parts) > 1 else None
        episode    = parts[2] if len(parts) > 2 else None
        is_series  = content_type == "series" and season and episode

        tmdb_id = await get_tmdb_id(content_id, content_type)
        if not tmdb_id:
            logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
            return result

        page_url = (
            f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            if is_series
            else f"{SC_DOMAIN}/movie/{tmdb_id}/"
        )
        logger.info(f"🎬 VixSrc page: {page_url}")

        # --- Modalità MediaFlow Proxy (nuova, prioritaria) ---
        if MEDIAFLOW_URL:
            m3u8_url = await extract_m3u8_from_vixsrc(page_url)
            if m3u8_url:
                stream_url = build_mediaflow_url(m3u8_url)
                logger.info(f"✅ MediaFlow stream: {stream_url[:80]}...")
                result["streams"].append({
                    "name": "🛸 UFO",
                    "title": "VixSrc • MediaFlow",
                    "url": stream_url,
                    "behaviorHints": {
                        "notWebReady": False,
                        "bingeGroup": "ufo-sc",
                    },
                })
            else:
                logger.error("❌ Impossibile estrarre m3u8 da VixSrc")
            return result

        # --- Modalità EasyProxy (legacy) ---
        if EASYPROXY_URL:
            stream_url = build_easyproxy_url(page_url)
            logger.info(f"✅ EasyProxy stream: {stream_url[:80]}...")
            result["streams"].append({
                "name": "🛸 UFO",
                "title": "VixSrc • EasyProxy",
                "url": stream_url,
                "behaviorHints": {
                    "notWebReady": False,
                    "bingeGroup": "ufo-sc",
                },
            })
            return result

        logger.error("❌ Nessun proxy configurato (MEDIAFLOW_URL o EASYPROXY_URL)")

    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
