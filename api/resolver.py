import re
import logging
from typing import Dict, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx

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
    "Referer": SC_DOMAIN,
    "Origin": SC_DOMAIN,
}

# --- Pattern per trovare .m3u8 nell'HTML ---
_M3U8_PATTERNS = [
    re.compile(r'(?:source|file|src)\s*:\s*["\']([^"\']+ \.m3u8[^"\']* )["\']', re.VERBOSE),
    re.compile(r'["\']([^"\']+ \.m3u8[^"\']* )["\']', re.VERBOSE),
    re.compile(r'(https?://[^\s"\' <>]+\.m3u8[^\s"\' <>]*)'),
]

# Pattern per trovare src di iframe
_IFRAME_RE = re.compile(r'<iframe[^>]+src=["\']([^"\' >]+)["\']', re.IGNORECASE)

# Pattern per trovare chiamate API VixSrc nei JS inline
# VixSrc di solito chiama /api/source/<id> o /api/episode/<id>
_API_RE = re.compile(r'(?:fetch|axios|XMLHttpRequest)[^;\n]*["\']([^"\' ]+/api/[^"\' ]+)["\']')
_SOURCE_ID_RE = re.compile(r'/(?:tv|movie)/(\d+)')


def _find_m3u8_in_html(html: str) -> Optional[str]:
    """Cerca il primo URL .m3u8 valido nell'HTML usando i pattern definiti."""
    for pattern in _M3U8_PATTERNS:
        m = pattern.search(html)
        if m:
            url = m.group(1).strip()
            if url.startswith("http"):
                return url
    return None


async def _fetch_html(client: httpx.AsyncClient, url: str, referer: str = SC_DOMAIN) -> Optional[str]:
    """Scarica una pagina e restituisce l'HTML, oppure None in caso di errore."""
    try:
        headers = {**BROWSER_HEADERS, "Referer": referer}
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"fetch fallito per {url}: {e}")
        return None


async def _try_vixsrc_api(client: httpx.AsyncClient, page_url: str, html: str) -> Optional[str]:
    """
    Step 3: prova le API interne di VixSrc.
    VixSrc espone endpoint del tipo:
      /api/source/<id>        -> POST, risponde con {data: [{file, label}]}
      /api/episode/<id>       -> GET, risponde con {source: [{file}]}
    Estrae l'ID dal path della pagina e prova entrambi.
    """
    base = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    # Cerca ID numerico nella URL della pagina
    id_match = _SOURCE_ID_RE.search(page_url)
    if not id_match:
        return None
    content_id = id_match.group(1)

    # Prova l'endpoint /api/source/<id> via POST (pattern comune VixSrc/SuperEmbed)
    api_url = f"{base}/api/source/{content_id}"
    try:
        resp = await client.post(
            api_url,
            headers={**BROWSER_HEADERS, "Referer": page_url, "X-Requested-With": "XMLHttpRequest"},
            data={"r": page_url, "d": urlparse(page_url).netloc},
        )
        if resp.status_code == 200:
            data = resp.json()
            # Formato: {"success": true, "data": [{"file": "...", "label": "720p"}]}
            sources = data.get("data") or data.get("source") or []
            for src in sources:
                f = src.get("file") or src.get("src") or ""
                if ".m3u8" in f:
                    logger.info(f"✅ m3u8 da API VixSrc ({api_url}): {f[:80]}")
                    return f
    except Exception as e:
        logger.debug(f"API VixSrc POST fallita: {e}")

    # Prova endpoint /api/episode/<id> via GET
    api_url2 = f"{base}/api/episode/{content_id}"
    try:
        resp = await client.get(
            api_url2,
            headers={**BROWSER_HEADERS, "Referer": page_url, "X-Requested-With": "XMLHttpRequest"},
        )
        if resp.status_code == 200:
            data = resp.json()
            sources = data.get("source") or data.get("data") or []
            for src in (sources if isinstance(sources, list) else [sources]):
                f = src.get("file") or src.get("src") or ""
                if ".m3u8" in f:
                    logger.info(f"✅ m3u8 da API VixSrc ({api_url2}): {f[:80]}")
                    return f
    except Exception as e:
        logger.debug(f"API VixSrc GET fallita: {e}")

    return None


async def extract_m3u8_from_vixsrc(page_url: str) -> Optional[str]:
    """
    Estrazione multi-step dell'URL .m3u8 da VixSrc:
      Step 1 - HTML diretto: cerca .m3u8 nell'HTML della pagina principale
      Step 2 - Iframe:       segue i src degli iframe trovati e cerca .m3u8
      Step 3 - API interna:  chiama /api/source/<id> e /api/episode/<id>
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:

        # STEP 1: HTML diretto
        logger.info(f"🔍 Step 1 - fetch HTML: {page_url}")
        html = await _fetch_html(client, page_url)
        if html:
            m3u8 = _find_m3u8_in_html(html)
            if m3u8:
                logger.info(f"✅ Step 1 trovato: {m3u8[:80]}")
                return m3u8

            # STEP 2: iframe
            iframes = _IFRAME_RE.findall(html)
            for iframe_src in iframes[:3]:  # max 3 iframe
                iframe_url = iframe_src if iframe_src.startswith("http") else urljoin(page_url, iframe_src)
                logger.info(f"🔍 Step 2 - iframe: {iframe_url[:80]}")
                iframe_html = await _fetch_html(client, iframe_url, referer=page_url)
                if iframe_html:
                    m3u8 = _find_m3u8_in_html(iframe_html)
                    if m3u8:
                        logger.info(f"✅ Step 2 trovato in iframe: {m3u8[:80]}")
                        return m3u8

            # STEP 3: API interna VixSrc
            logger.info(f"🔍 Step 3 - API VixSrc")
            m3u8 = await _try_vixsrc_api(client, page_url, html)
            if m3u8:
                return m3u8

    logger.warning(f"⚠️ Nessun .m3u8 trovato con tutti i metodi per: {page_url}")
    return None


def build_easyproxy_url(vixsrc_page_url: str) -> str:
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


def build_mediaflow_url(m3u8_url: str) -> str:
    encoded = quote(m3u8_url, safe="")
    url = f"{MEDIAFLOW_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if MEDIAFLOW_PSW:
        url += f"&api_password={quote(MEDIAFLOW_PSW, safe='')}"
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

        # ===== PRIORITA' 1: EasyProxy (se EASYPROXY_URL e' impostato) =====
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

        # ===== PRIORITA' 2: MediaFlow Proxy con estrazione m3u8 multi-step =====
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
                logger.error("❌ Impossibile estrarre m3u8 da VixSrc (tutti i metodi falliti)")
            return result

        logger.error("❌ Nessun proxy configurato: imposta EASYPROXY_URL o MEDIAFLOW_URL")

    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
