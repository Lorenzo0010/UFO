"""
vidxgo.py — Provider VidXgo per UFO.

URL pattern (movie):  {VD_DOMAIN}/{imdb_id}
URL pattern (series): {VD_DOMAIN}/{imdb_id}/{season}/{episode}

Resolver nativo (port di StreamVix/src/extractors/vidxgo.ts):
  1. GET della pagina embed con User-Agent Firefox-150 e Referer altadefinizione.you
  2. Parsing del 6° <script> tag (index 5) con regex (nessun DOM, zero overhead)
  3. Regex `var <name>='KEY',d=atob('B64')` → XOR ciclico byte-by-byte con KEY
  4. Nel JS decrittato cerca `currentSrc+"https://..."` → URL M3U8 finale
  5. Gli header di playback vengono passati al proxy interno via ?headers=<base64-JSON>
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

VD_DOMAIN: str = os.getenv("VIDXGO_DOMAIN", "https://v.vidxgo.co").rstrip("/")
VIDXGO_ENABLED: bool = os.getenv("VIDXGO_ENABLED", "1").lower() not in ("0", "false", "off", "no")

# ---------------------------------------------------------------------------
# Headers embed-page GET — copiati 1:1 da StreamVix/vidxgo.ts
# ---------------------------------------------------------------------------

_EMBED_HEADERS: dict = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "DNT": "1",
    "Referer": "https://altadefinizione.you/",
    "Priority": "u=0, i",
}

# Headers di playback (Chrome UA) — passati al proxy HLS interno via ?headers=
_PLAYBACK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def _playback_headers(domain: str) -> dict:
    return {
        "User-Agent": _PLAYBACK_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": domain.rstrip("/") + "/",
        "Origin": domain.rstrip("/"),
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "DNT": "1",
    }


# ---------------------------------------------------------------------------
# Logica di decrittazione — port di decodeVidXgoHtml (vidxgo.ts)
# ---------------------------------------------------------------------------

# Regex per estrarre tutti i <script> tag (inline + external)
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>([\s\S]*?)<\/script>", re.IGNORECASE)
# Regex per riconoscere script esterni (hanno `src=`)
_SCRIPT_SRC_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)
# Regex chiave XOR + payload base64 nel 6° script
_KEY_PAYLOAD_RE = re.compile(r"var\s+\w+\s*=\s*'([^']*)'\s*,\s*d\s*=\s*atob\(\s*'([^']*)'")
# Regex URL HLS nel JS decrittato
_CURRENTSRC_RE = re.compile(r'currentSrc.+?"(https:[^";]+)"')


def _decode_vidxgo_html(html: str) -> Optional[str]:
    """
    Estrae l'URL M3U8 dall'HTML della pagina embed di VidXgo.
    Port Python di StreamVix decodeVidXgoHtml (TypeScript).
    Restituisce None se qualsiasi step fallisce.
    """
    # Raccoglie i body degli script mantenendo gli slot degli script esterni
    script_bodies: list[str] = []
    for m in _SCRIPT_RE.finditer(html):
        attrs = m.group(1) or ""
        if _SCRIPT_SRC_RE.search(attrs):
            script_bodies.append("")           # esterno: slot vuoto, come in StreamVix
        else:
            script_bodies.append(m.group(2) or "")

    if len(script_bodies) <= 5:
        logger.warning("[VidXgo][decode] trovati solo %d <script> tag (attesi >5)", len(script_bodies))
        return None

    target = script_bodies[5]
    if not target:
        logger.warning("[VidXgo][decode] script[5] è vuoto")
        return None

    km = _KEY_PAYLOAD_RE.search(target)
    if not km:
        logger.warning("[VidXgo][decode] regex chiave/payload non trovata in script[5]")
        return None

    key = km.group(1)
    b64 = km.group(2)
    if not key or not b64:
        return None

    try:
        decoded = base64.b64decode(b64 + "==")
    except Exception as e:
        logger.warning("[VidXgo][decode] base64 decode fallita: %s", e)
        return None

    # XOR ciclico con la chiave
    key_bytes = key.encode("utf-8")
    key_len = len(key_bytes)
    decrypted = bytes(b ^ key_bytes[i % key_len] for i, b in enumerate(decoded))

    try:
        decrypted_str = decrypted.decode("utf-8")
    except UnicodeDecodeError:
        decrypted_str = decrypted.decode("latin-1")

    url_match = _CURRENTSRC_RE.search(decrypted_str)
    if not url_match:
        logger.warning("[VidXgo][decode] nessun URL currentSrc trovato nel JS decrittato")
        return None

    return url_match.group(1).replace("\\", "")


# ---------------------------------------------------------------------------
# Fetch + estrazione
# ---------------------------------------------------------------------------

async def _fetch_and_extract(embed_url: str) -> Optional[tuple[str, dict]]:
    """
    Fetcha la pagina embed VidXgo ed estrae (m3u8_url, playback_headers).
    Restituisce None in caso di errore.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=_EMBED_HEADERS)
    except httpx.RequestError as e:
        logger.error("[VidXgo] fetch error per %s: %s", embed_url, e)
        return None

    if resp.status_code != 200:
        logger.warning("[VidXgo] HTTP %d per %s", resp.status_code, embed_url)
        return None

    m3u8 = _decode_vidxgo_html(resp.text)
    if not m3u8:
        return None

    # Dominio per gli header di playback (Origin/Referer CDN)
    from urllib.parse import urlparse
    parsed = urlparse(embed_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"

    return m3u8, _playback_headers(domain)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode_headers_b64(headers: dict) -> str:
    """Serializza un dict di header in base64-JSON per il param ?headers=."""
    return base64.b64encode(json.dumps(headers).encode()).decode().rstrip("=")


def _build_embed_url(imdb_id: str, season: Optional[str], episode: Optional[str], is_movie: bool) -> str:
    clean = imdb_id.split(":")[0]
    if is_movie or not season or not episode:
        return f"{VD_DOMAIN}/{clean}"
    return f"{VD_DOMAIN}/{clean}/{season}/{episode}"


def _build_proxy_url(m3u8_url: str, headers: dict, addon_base_url: str) -> str:
    """Wrappa l'URL M3U8 nel proxy interno UFO con header di playback."""
    from urllib.parse import quote
    base = addon_base_url.rstrip("/")
    encoded_url = quote(m3u8_url, safe="")
    encoded_headers = quote(_encode_headers_b64(headers), safe="")
    return f"{base}/proxy/manifest.m3u8?url={encoded_url}&headers={encoded_headers}"


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

async def resolve_vidxgo(
    imdb_id: str,
    content_label: str,
    content_type: str,
    season: Optional[str],
    episode: Optional[str],
    addon_base_url: str,
) -> Optional[Dict]:
    """
    Risolve VidXgo estraendo nativamente l'URL M3U8 dalla pagina embed.
    Restituisce un dict stream Stremio oppure None se non disponibile.
    """
    if not VIDXGO_ENABLED:
        logger.debug("[VidXgo] disabilitato (VIDXGO_ENABLED=0)")
        return None

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.info("[VidXgo] skip — IMDB ID mancante o non valido: %r", imdb_id)
        return None

    is_movie = content_type == "movie"
    embed_url = _build_embed_url(imdb_id, season, episode, is_movie)

    logger.info("[VidXgo] risoluzione embed: %s", embed_url)
    result = await _fetch_and_extract(embed_url)

    if not result:
        logger.warning("[VidXgo] estrazione fallita per %s", embed_url)
        return None

    m3u8_url, pb_headers = result
    stream_url = _build_proxy_url(m3u8_url, pb_headers, addon_base_url)

    logger.info("[VidXgo] ✅ M3U8 estratto → proxy: %s", stream_url[:100])

    binge_group = "ufo-vidxgo-movie" if is_movie else f"ufo-vidxgo-s{season}e{episode}"
    return {
        "name": "UFO\n🌍 VidXgo",
        "title": content_label,
        "url": stream_url,
        "behaviorHints": {
            "notWebReady": True,
            "bingeGroup": binge_group,
        },
    }
