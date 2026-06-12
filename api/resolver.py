"""
resolver.py — estrazione M3U8 da VixSrc/VixCloud via fetch HTTP puro.

Logica portata da streamvix/src/extractors/vixcloud.ts:
  1. GET sulla pagina VixSrc (/tv/ o /movie/)
  2. Cerca un tag <iframe> con src che punta a vixcloud.*
  3. GET sull'URL embed VixCloud
  4. Trova lo script inline con `window.masterPlaylist`
  5. Parsa masterPlaylist.url + params.token + params.expires -> URL HLS finale
  6. Aggiunge &h=1 se canPlayFHD === true

Nessun Playwright, nessun browser headless. Puro httpx async.
"""

import asyncio
import json
import logging
import re
from typing import Dict, Optional
from urllib.parse import quote, urlencode, urlparse

import httpx

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW, USER_AGENT
from .tmdb import get_tmdb_info, get_episode_title

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "*/*",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "User-Agent": USER_AGENT or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
    ),
}

_TIMEOUT = httpx.Timeout(20.0)


# ---------------------------------------------------------------------------
# EasyProxy helper
# ---------------------------------------------------------------------------

def build_easyproxy_url(m3u8_url: str) -> str:
    encoded = quote(m3u8_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


# ---------------------------------------------------------------------------
# Step 1 — ricava l'URL embed VixCloud dalla pagina VixSrc
# ---------------------------------------------------------------------------

async def _get_vixcloud_embed(vixsrc_url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Carica la pagina VixSrc e cerca un <iframe src="...vixcloud..."> o
    direttamente un tag script con masterPlaylist (se la pagina è già l'embed).
    """
    resp = await client.get(vixsrc_url, headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Cerca iframe che punta a vixcloud
    m = re.search(r'<iframe[^>]+src=["\']([^"\']*vixcloud[^"\']*)["\']', html, re.IGNORECASE)
    if m:
        embed_url = m.group(1)
        if embed_url.startswith("//"):
            embed_url = "https:" + embed_url
        logger.info(f"🔗 VixCloud embed trovato: {embed_url[:100]}")
        return embed_url

    # Fallback: la pagina stessa contiene masterPlaylist (embed diretto)
    if "masterPlaylist" in html:
        logger.info("🔗 masterPlaylist trovato direttamente nella pagina VixSrc")
        return vixsrc_url

    logger.warning(f"⚠️ Nessun iframe VixCloud trovato in {vixsrc_url}")
    return None


# ---------------------------------------------------------------------------
# Step 2 — estrai M3U8 dall'embed VixCloud (porta di vixcloud.ts)
# ---------------------------------------------------------------------------

def _sanitise_and_parse_window_vars(script: str) -> Optional[dict]:
    """
    Replica getSanitisedScript() di vixcloud.ts:
    split su ogni `window.VAR = ` e ricostruisce un JSON aggregato.
    """
    raw = script.replace("\n", "\t")

    key_re = re.compile(r"window\.(\w+)\s*=\s*")
    keys = key_re.findall(raw)
    parts = key_re.split(raw)[1:]  # drop il testo prima del primo `window.`

    # key_re.split restituisce: [testo_pre, key1, parte1, key2, parte2, ...]
    # Usiamo findall per i nomi e splittiamo sul pattern per i valori
    value_parts = re.split(r"window\.\w+\s*=\s*", raw)[1:]

    if not keys or len(keys) != len(value_parts):
        logger.debug(f"[vixcloud] key/parts mismatch keys={len(keys)} parts={len(value_parts)}")
        return None

    json_objects = []
    for key, part in zip(keys, value_parts):
        cleaned = part
        cleaned = re.sub(r";", "", cleaned)
        cleaned = re.sub(r'([{\[,])\s*(\w+)\s*:', r'\1 "\2":', cleaned)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
        cleaned = cleaned.strip()
        cleaned = cleaned.replace("'", '"')
        json_objects.append(f'"{key}": {cleaned}')

    aggregated = "{\n" + ",\n".join(json_objects) + "\n}"

    try:
        return json.loads(aggregated)
    except json.JSONDecodeError as e:
        logger.debug(f"[vixcloud] JSON parse fail: {e} | snippet: {aggregated[:200]}")
        return None


async def _extract_m3u8_from_embed(embed_url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Porta diretta di VixCloudHlsExtractor.extract() da vixcloud.ts.
    """
    resp = await client.get(embed_url, headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Trova il <script> che contiene masterPlaylist
    script_tag = None
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
        if "masterPlaylist" in m.group(1):
            script_tag = m.group(1)
            break

    if not script_tag:
        logger.warning(f"[vixcloud] Nessuno script con masterPlaylist in {embed_url[:80]}")
        return None

    parsed = _sanitise_and_parse_window_vars(script_tag)
    if not parsed:
        # Fallback: regex diretta sull'URL m3u8 se la parse strutturata fallisce
        m = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', script_tag)
        if m:
            logger.info(f"[vixcloud] Fallback regex URL: {m.group(1)[:80]}")
            return m.group(1)
        return None

    master = parsed.get("masterPlaylist")
    if not master:
        logger.warning("[vixcloud] masterPlaylist mancante nel JSON parsato")
        return None

    base_url: str = master.get("url", "")
    if not base_url:
        logger.warning("[vixcloud] masterPlaylist.url vuoto")
        return None

    params_obj = master.get("params", {}) or {}
    token = params_obj.get("token", "")
    expires = params_obj.get("expires", "")

    param_str = f"token={quote(str(token), safe='')}&expires={quote(str(expires), safe='')}"

    # Assicura suffisso .m3u8
    before_query = base_url.split("?")[0]
    if not re.search(r"\.m3u8$", before_query, re.IGNORECASE):
        query_part = base_url[len(before_query):]
        base_url = before_query.rstrip("/") + ".m3u8" + query_part

    if "?" in base_url:
        final_url = base_url + "&" + param_str
    else:
        final_url = base_url + "?" + param_str

    if parsed.get("canPlayFHD") is True:
        final_url += "&h=1"

    logger.info(f"✅ VixCloud M3U8: {final_url[:120]}")
    return final_url


# ---------------------------------------------------------------------------
# Funzione principale: VixSrc URL -> M3U8
# ---------------------------------------------------------------------------

async def extract_m3u8(page_url: str) -> Optional[str]:
    """
    Dato un URL VixSrc (/tv/... o /movie/...) restituisce l'URL M3U8 HLS.
    Usa fetch HTTP puro (niente browser headless).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            embed_url = await _get_vixcloud_embed(page_url, client)
            if not embed_url:
                return None
            return await _extract_m3u8_from_embed(embed_url, client)
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ HTTP {e.response.status_code} per {e.request.url}")
        except httpx.RequestError as e:
            logger.error(f"❌ Request error: {e}")
        except Exception as e:
            logger.error(f"❌ extract_m3u8 errore inatteso: {e}")
    return None


# ---------------------------------------------------------------------------
# Entry point Stremio
# ---------------------------------------------------------------------------

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
                logger.error("❌ EASYPROXY_URL non configurato")
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
                logger.error("❌ EASYPROXY_URL non configurato")
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
