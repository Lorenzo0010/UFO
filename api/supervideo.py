"""
supervideo.py — Extractor per SuperVideo (supervideo.tv).

Flusso:
  1. GET della pagina embed (es. https://supervideo.tv/e/<id>)
  2. Trova il tag <script> con eval(function(p,a,c,k,e,d){...}) (p.a.c.k.e.r)
  3. Deoffusca con l'unpacker di mixdrop.py
  4. Regex sources:[{file:"<url>"  sul codice deoffuscato
  5. Restituisce l'URL HLS M3U8 come stream Stremio

Ported dalla logica di streamvix SuperVideoExtractor.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote as _quote

import httpx
from bs4 import BeautifulSoup, SoupStrainer

from .mixdrop import _packer_detect, _packer_unpack, _UnpackingError
from .proxy import encode_headers_b64

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(25.0)

_SUPERVIDEO_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

# Pattern per trovare il file HLS dentro il codice unpacked
_SOURCES_FILE_RE = re.compile(r'sources:\[\{file:"(.*?)"')


# ---------------------------------------------------------------------------
# Supporto: verifica se un URL è di SuperVideo
# ---------------------------------------------------------------------------

def is_supervideo_url(url: str) -> bool:
    """Restituisce True se l'URL è un embed SuperVideo."""
    return "supervideo" in url.lower()


# ---------------------------------------------------------------------------
# Estrazione M3U8 da SuperVideo
# ---------------------------------------------------------------------------

async def _extract_supervideo_m3u8(embed_url: str) -> Optional[str]:
    """
    Scarica la pagina embed di SuperVideo, deoffusca il p.a.c.k.e.r
    e cerca sources:[{file:"<url>"}.
    Restituisce l'URL HLS M3U8, oppure None.
    """
    # Normalizza URL: forza dominio .tv e path /e/
    url = embed_url
    if url.startswith("//"):
        url = "https:" + url

    # Estrai l'ID dall'URL ed usa il dominio canonico
    parts = url.rstrip("/").split("/")
    video_id = parts[-1] if parts else ""
    if not video_id:
        return None

    canonical_url = f"https://supervideo.tv/e/{video_id}"
    referer = "https://supervideo.tv/"

    headers = {
        "User-Agent": _SUPERVIDEO_UA,
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    logger.info(f"[SuperVideo] ▶️  fetch embed: {canonical_url}")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(canonical_url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"[SuperVideo] HTTP {resp.status_code} per {canonical_url}")
                return None
    except Exception as e:
        logger.warning(f"[SuperVideo] errore fetch per {canonical_url}: {e}")
        return None

    html = resp.text

    # Cloudflare check
    if "Cloudflare" in html or "Just a moment" in html:
        logger.warning(f"[SuperVideo] Cloudflare challenge per {canonical_url}")
        return None

    # Cerca script p.a.c.k.e.r
    soup = BeautifulSoup(html, "lxml", parse_only=SoupStrainer("script"))
    for tag in soup.find_all("script"):
        text = tag.get_text()
        if _packer_detect(text):
            try:
                unpacked = _packer_unpack(text)
                m = _SOURCES_FILE_RE.search(unpacked)
                if m:
                    stream_url = m.group(1)
                    if stream_url.startswith("//"):
                        stream_url = "https:" + stream_url
                    logger.info(f"[SuperVideo] ✅ M3U8 trovato: {stream_url[:100]}")
                    return stream_url
            except _UnpackingError as e:
                logger.debug(f"[SuperVideo] unpack error: {e}")

    logger.info(f"[SuperVideo] ℹ️  nessun stream trovato per {canonical_url}")
    return None


# ---------------------------------------------------------------------------
# Funzione pubblica: resolve_supervideo
# ---------------------------------------------------------------------------

async def resolve_supervideo(
    embed_url: str,
    content_label: str,
    source_name: str,
    addon_base_url: str,
    resolution_hint: str = "",
) -> Optional[dict]:
    """
    Risolve un link embed SuperVideo in uno stream Stremio.

    Args:
        embed_url:       URL embed SuperVideo
        content_label:   Titolo del film/episodio
        source_name:     Nome del provider (es. "GuardaHD")
        addon_base_url:  Base URL del proxy UFO
        resolution_hint: Hint risoluzione (es. "1080p", "720p")

    Returns:
        dict stream Stremio oppure None se fallisce.
    """
    m3u8_url = await _extract_supervideo_m3u8(embed_url)
    if not m3u8_url:
        return None

    # SuperVideo serve HLS — wrappalo nel proxy con headers appropriati
    # Calcola il referer dal dominio dell'M3U8
    try:
        from urllib.parse import urlparse
        parsed = urlparse(m3u8_url)
        playback_referer = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        playback_referer = "https://supervideo.tv/"

    playback_headers = {
        "User-Agent": _SUPERVIDEO_UA,
        "Referer": playback_referer,
        "Origin": playback_referer.rstrip("/"),
    }
    headers_b64 = encode_headers_b64(playback_headers)

    base = addon_base_url.rstrip("/")
    proxy_url = f"{base}/proxy/manifest.m3u8?url={_quote(m3u8_url, safe='')}&headers={headers_b64}"

    res_label = f" {resolution_hint}" if resolution_hint else ""

    return {
        "name": f"UFO\n🎬 {source_name}",
        "title": f"{content_label}\n▶️ SuperVideo{res_label}",
        "url": proxy_url,
        "behaviorHints": {
            "notWebReady": True,
            "bingeGroup": f"ufo-{source_name.lower()}-sv",
        },
    }
