import re
import logging
from typing import Dict, Optional
from urllib.parse import quote

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup, SoupStrainer

from .config import SC_DOMAIN, USER_AGENT
from .tmdb import get_tmdb_id

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": SC_DOMAIN + "/",
    "Origin": SC_DOMAIN,
}


async def extract_vixsrc_stream(page_url: str) -> Optional[str]:
    """
    Estrae il vero URL dello stream da VixSrc leggendo token, expires
    e server_url dallo script inline della pagina.
    """
    async with AsyncSession() as client:
        try:
            headers = {**BROWSER_HEADERS, "Referer": SC_DOMAIN + "/"}
            resp = await client.get(page_url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"VixSrc fetch fallito: {resp.status_code} per {page_url}")
                return None

            soup = BeautifulSoup(resp.text, "lxml", parse_only=SoupStrainer("body"))
            if not soup:
                return None

            script_tag = soup.find("body").find("script")
            if not script_tag:
                logger.warning("Nessun <script> trovato nel body")
                return None

            script = script_tag.text

            token_m   = re.search(r"'token':\s*'(\w+)'", script)
            expires_m = re.search(r"'expires':\s*'(\d+)'", script)
            server_m  = re.search(r"url:\s*'([^']+)'", script)

            if not (token_m and expires_m and server_m):
                logger.warning("Token/expires/server_url non trovati nello script")
                return None

            token      = token_m.group(1)
            expires    = expires_m.group(1)
            server_url = server_m.group(1)

            if "?b=1" in server_url:
                final_url = f"{server_url}&token={token}&expires={expires}"
            else:
                final_url = f"{server_url}?token={token}&expires={expires}"

            if "window.canPlayFHD = true" in script:
                final_url += "&h=1"

            parts = final_url.split("?")
            final_url = parts[0] + ".m3u8" + "?" + parts[1]

            logger.info(f"✅ VixSrc stream estratto: {final_url[:80]}")
            return final_url

        except Exception as e:
            logger.error(f"❌ extract_vixsrc_stream error: {e}")
            return None


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

        m3u8_url = await extract_vixsrc_stream(page_url)
        if m3u8_url:
            result["streams"].append({
                "name": "🛸 UFO 🇮🇹",
                "title": "VixSrc • Diretto",
                "url": m3u8_url,
                "behaviorHints": {
                    "notWebReady": True,
                    "proxyHeaders": {
                        "request": {
                            "User-Agent": USER_AGENT,
                            "Referer": page_url,
                            "Origin": SC_DOMAIN,
                        }
                    },
                    "bingeGroup": "ufo-sc",
                },
            })
        else:
            logger.error("❌ Stream diretto non disponibile")

    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
