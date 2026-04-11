import re
import logging
from typing import Dict, Optional
from urllib.parse import quote, urljoin, urlparse
import urllib.parse

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup, SoupStrainer

from .config import (
    SC_DOMAIN, USER_AGENT,
    EASYPROXY_URL, EASYPROXY_PSW,
    MEDIAFLOW_URL, MEDIAFLOW_PSW,
)
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
    Estrae il vero URL dello stream da VixSrc usando la stessa tecnica di MammaMia:
    legge token, expires e server_url dallo script inline della pagina.
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

            # Aggiungi estensione .m3u8 come fa MammaMia
            parts = final_url.split("?")
            final_url = parts[0] + ".m3u8" + "?" + parts[1]

            logger.info(f"✅ VixSrc stream estratto: {final_url[:80]}")
            return final_url

        except Exception as e:
            logger.error(f"❌ extract_vixsrc_stream error: {e}")
            return None


def build_easyproxy_url(vixsrc_page_url: str) -> str:
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


async def build_mediaflow_url(m3u8_url: str, page_url: str) -> Optional[str]:
    """
    Costruisce l'URL MediaFlow Proxy usando l'endpoint /extractor/video
    esattamente come fa MammaMia (mfp.py: build_mfp + transform_mfp).
    """
    mfp_url = f"{MEDIAFLOW_URL}/extractor/video?api_password={quote(MEDIAFLOW_PSW, safe='')}&d={quote(m3u8_url, safe='')}&host=VixCloud&redirect_stream=false"
    try:
        async with AsyncSession() as client:
            resp = await client.get(mfp_url)
            data = resp.json()
            url = (
                data["mediaflow_proxy_url"]
                + "?api_password=" + data["query_params"]["api_password"]
                + "&d=" + urllib.parse.quote(data["destination_url"])
            )
            for key, val in data.get("request_headers", {}).items():
                url += f"&h_{key}={urllib.parse.quote(val)}"
            logger.info(f"✅ MediaFlow URL costruito: {url[:80]}")
            return url
    except Exception as e:
        logger.warning(f"build_mediaflow_url fallito, fallback diretto: {e}")
        # Fallback: costruzione manuale con header Referer
        encoded = quote(m3u8_url, safe="")
        url = f"{MEDIAFLOW_URL}/proxy/hls/manifest.m3u8?d={encoded}"
        if MEDIAFLOW_PSW:
            url += f"&api_password={quote(MEDIAFLOW_PSW, safe='')}"
        url += f"&h_Referer={quote(page_url, safe='')}&h_Origin={quote(SC_DOMAIN, safe='')}&h_User-Agent={quote(USER_AGENT, safe='')}"
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

        # ===== PRIORITA' 1: EasyProxy =====
        if EASYPROXY_URL:
            stream_url = build_easyproxy_url(page_url)
            logger.info(f"✅ EasyProxy stream: {stream_url[:80]}")
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

        # ===== PRIORITA' 2: MediaFlow Proxy =====
        if MEDIAFLOW_URL:
            m3u8_url = await extract_vixsrc_stream(page_url)
            if m3u8_url:
                stream_url = await build_mediaflow_url(m3u8_url, page_url)
                if stream_url:
                    logger.info(f"✅ MediaFlow stream: {stream_url[:80]}")
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
                logger.error("❌ Impossibile estrarre stream da VixSrc")
            return result

        # ===== NESSUN PROXY: stream diretto (potrebbe non funzionare fuori LAN) =====
        m3u8_url = await extract_vixsrc_stream(page_url)
        if m3u8_url:
            result["streams"].append({
                "name": "🛸 UFO",
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
            logger.error("❌ Nessun proxy configurato e stream diretto fallito")

    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
