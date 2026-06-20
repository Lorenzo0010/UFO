"""
guardahd.py — Provider GuardaHD per UFO (solo film).

Flusso:
  1. Chiama https://guardahd.stream/set-movie-a/{imdb_id}  (endpoint principale)
     Se non trova link → riprova con /set-movie/{imdb_id}  (endpoint alternativo)
  2. Estrae iframe/data-link con host MixDrop, StreamHG (dhcplay/vibuxer)
  3. Per ogni link valido, estrae l'URL .m3u8 diretto
  4. Restituisce stream Stremio (senza proxy — stream diretti con headers)

Note:
  - Solo film (GuardaHD non ha serie TV affidabili)
  - Richiede IMDB ID (tt...)
  - GUARDAHD_ENABLED=0 per disabilitare
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

GUARDAHD_ENABLED: bool = os.getenv("GUARDAHD_ENABLED", "1").lower() not in ("0", "false", "off", "no")
GUARDAHD_BASE: str = "https://guardahd.stream"

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
_TIMEOUT = httpx.Timeout(20.0)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": GUARDAHD_BASE,
}

# Endpoint da provare in ordine
_GUARDAHD_ENDPOINTS = [
    "/set-movie-a/{imdb_id}",
    "/set-movie/{imdb_id}",
]


# ---------------------------------------------------------------------------
# Extractor helpers
# ---------------------------------------------------------------------------

def _unpack(p: str, a: int, c: int, k: list) -> str:
    """Dean Edward's p,a,c,k,e,d unpacker."""
    def _lookup(c2: int) -> str:
        base = ""
        if c2 >= a:
            base = _lookup(c2 // a)
        r = c2 % a
        if r > 35:
            suffix = chr(r + 29)
        else:
            suffix = str(r) if r < 10 else chr(r + 87)
        return base + suffix

    d: dict = {}
    for i in range(c - 1, -1, -1):
        if k[i]:
            d[_lookup(i)] = k[i]
        else:
            d[_lookup(i)] = _lookup(i)

    result = re.sub(r'\b(\w+)\b', lambda m: d.get(m.group(1), m.group(1)), p)
    return result


def _extract_packed_stream(html: str) -> Optional[str]:
    """Estrae .m3u8 da codice eval/pack."""
    pattern = re.compile(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)",
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    try:
        p, a, c, k = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|")
        unpacked = _unpack(p, a, c, k)
        wurl = re.search(r'wurl\s*=\s*["\']( https?://[^"\']+)["\']', unpacked)
        if wurl:
            url = wurl.group(1).strip()
            return ("https:" + url) if url.startswith("//") else url
        # fallback: cerca direttamente un .m3u8 nell'unpacked
        m3u8 = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', unpacked)
        if m3u8:
            return m3u8.group(1)
    except Exception as e:
        logger.debug(f"[GuardaHD] unpack error: {e}")
    return None


async def _extract_mixdrop(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Estrae stream URL da MixDrop."""
    try:
        if url.startswith("//"):
            url = "https:" + url
        resp = await client.get(url, headers={**_HEADERS, "Referer": "https://mixdrop.sb/"})
        if not resp.is_success:
            logger.debug(f"[GuardaHD] MixDrop HTTP {resp.status_code} per {url}")
            return None
        html = resp.text
        stream = _extract_packed_stream(html)
        if stream:
            return stream
        # fallback: cerca iframe /e/
        iframe = re.search(r'<iframe[^>]+src=["\']( /e/[^"\']+)["\']', html)
        if iframe:
            embed_url = url.split("/f/")[0].rstrip("/") + iframe.group(1).strip()
            resp2 = await client.get(embed_url, headers={**_HEADERS, "Referer": url})
            if resp2.is_success:
                return _extract_packed_stream(resp2.text)
    except Exception as e:
        logger.debug(f"[GuardaHD] MixDrop error: {e}")
    return None


async def _extract_streamhg(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Estrae stream URL da StreamHG (dhcplay/vibuxer)."""
    try:
        if url.startswith("//"):
            url = "https:" + url
        resp = await client.get(
            url,
            headers={**_HEADERS, "Referer": url},
            follow_redirects=True,
        )
        if not resp.is_success:
            logger.debug(f"[GuardaHD] StreamHG HTTP {resp.status_code} per {url}")
            return None
        stream = _extract_packed_stream(resp.text)
        if stream:
            return stream
    except Exception as e:
        logger.debug(f"[GuardaHD] StreamHG error: {e}")
    return None


# ---------------------------------------------------------------------------
# Core: fetch GuardaHD page e raccogli link
# ---------------------------------------------------------------------------

def _parse_links_from_html(html: str) -> List[str]:
    """Estrae tutti i link candidati dall'HTML di GuardaHD."""
    links: set = set()

    # iframe src
    for m in re.finditer(r'<iframe[^>]+src=["\']( https?://[^"\']+)["\']', html, re.IGNORECASE):
        links.add(m.group(1).strip())

    # data-link
    for m in re.finditer(r'data-link=["\']( https?://[^"\']+)["\']', html):
        links.add(m.group(1).strip())

    # URL diretti noti
    direct_re = re.compile(
        r'https?://(?:www\.)?(?:mixdrop|m1xdrop|dhcplay|vibuxer|loadm|uqload)[\w./-]+',
        re.IGNORECASE,
    )
    for m in direct_re.finditer(html):
        links.add(m.group(0))

    return list(links)


async def _fetch_guardahd_links(imdb_id: str) -> List[str]:
    """
    Prova gli endpoint GuardaHD in ordine e restituisce tutti i link candidati.
    Tenta /set-movie-a/{imdb_id} prima, poi /set-movie/{imdb_id} come fallback.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for endpoint_tpl in _GUARDAHD_ENDPOINTS:
            path = endpoint_tpl.format(imdb_id=imdb_id)
            url = f"{GUARDAHD_BASE}{path}"
            try:
                resp = await client.get(url, headers=_HEADERS)
                if not resp.is_success:
                    logger.warning(f"[GuardaHD] HTTP {resp.status_code} per {url}")
                    continue

                links = _parse_links_from_html(resp.text)
                if links:
                    logger.info(f"[GuardaHD] {len(links)} link trovati via {path}")
                    return links
                else:
                    logger.debug(f"[GuardaHD] nessun link in {path}, provo endpoint alternativo")

            except Exception as e:
                logger.warning(f"[GuardaHD] fetch error per {url}: {e}")
                continue

    logger.warning(f"[GuardaHD] nessun link trovato per {imdb_id} su nessun endpoint")
    return []


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

async def resolve_guardahd(
    imdb_id: str,
    content_label: str,
    content_type: str,
    addon_base_url: str,
) -> List[Dict]:
    """
    Restituisce una lista di stream Stremio da GuardaHD.
    Lanciato in parallelo con VixCloud e VidXgo — non è un fallback.
    Solo film (content_type == "movie").
    """
    if not GUARDAHD_ENABLED:
        logger.debug("[GuardaHD] disabilitato (GUARDAHD_ENABLED=0)")
        return []

    if content_type != "movie":
        logger.debug("[GuardaHD] solo film — skip per series")
        return []

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.info(f"[GuardaHD] skip — IMDB ID mancante: {imdb_id!r}")
        return []

    logger.info(f"[GuardaHD] 🎬 cerco stream per {imdb_id} ({content_label})")

    links = await _fetch_guardahd_links(imdb_id)
    if not links:
        logger.warning(f"[GuardaHD] ❌ nessun link trovato per {imdb_id}")
        return []

    streams: List[Dict] = []
    seen_urls: set = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:

        async def _process(link: str):
            stream_url: Optional[str] = None
            host_label = "Stream"
            try:
                if any(h in link for h in ("mixdrop", "m1xdrop")):
                    logger.info(f"[GuardaHD] MixDrop: {link}")
                    stream_url = await _extract_mixdrop(link, client)
                    host_label = "MixDrop"
                elif any(h in link for h in ("dhcplay", "vibuxer")):
                    logger.info(f"[GuardaHD] StreamHG: {link}")
                    stream_url = await _extract_streamhg(link, client)
                    host_label = "StreamHG"
                else:
                    logger.debug(f"[GuardaHD] host non supportato: {link}")
                    return
            except Exception as e:
                logger.debug(f"[GuardaHD] errore processo {link}: {e}")
                return

            if stream_url and stream_url not in seen_urls:
                seen_urls.add(stream_url)
                streams.append({
                    "name": "UFO\n🇮🇹 GuardaHD",
                    "title": f"{content_label}\n[{host_label}]",
                    "url": stream_url,
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": "ufo-guardahd",
                    },
                })
                logger.info(f"[GuardaHD] ✅ stream trovato via {host_label}: {stream_url[:80]}")
            elif stream_url:
                logger.debug(f"[GuardaHD] stream duplicato ignorato: {stream_url[:60]}")
            else:
                logger.debug(f"[GuardaHD] nessun stream estratto da: {link}")

        await asyncio.gather(*[_process(link) for link in links])

    if not streams:
        logger.warning(f"[GuardaHD] ❌ nessun stream estratto per {imdb_id} (link trovati: {len(links)})")
    else:
        logger.info(f"[GuardaHD] ✅ {len(streams)} stream trovati per {imdb_id}")

    return streams
