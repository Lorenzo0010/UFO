"""
resolver.py — estrazione M3U8 da VixSrc/VixCloud + VidXgo.

Flusso per VixSrc/VixCloud (invariato):
  1. GET /api/tv/<tmdb>/<s>/<e> oppure /api/movie/<tmdb>
  2. Estrai URL embed VixCloud
  3. Estrai token/expires/url dallo script embed
  4. Costruisci URL HLS con token, expires, [h=1 se canPlayFHD]
  5. Verifica HEAD su VixSrc (skip con VIXSRC_SKIP_LIST_CHECK=1)

Flusso per VidXgo (invariato):
  - Richiede IMDB ID (risolto da TMDB)
  - Costruisce {VIDXGO_DOMAIN}/{imdb_id}[/{s}/{e}]
  - Passa l'URL al proxy HLS interno (come VixCloud)

Entrambi i provider vengono lanciati con asyncio.gather().
Tutti gli stream validi vengono restituiti insieme.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

from .config import SC_DOMAIN, USER_AGENT
from .proxy import encode_headers_b64
from .tmdb import get_tmdb_info, get_episode_title
from .vidxgo import resolve_vidxgo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

_UA = USER_AGENT or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

_HEADERS: dict = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "User-Agent": _UA,
}

_TIMEOUT = httpx.Timeout(20.0)

_IFRAME_VIXCLOUD_RE = re.compile(
    r'<iframe[^>]+src=["\'](["\']*vixcloud[^"\']*)["\']',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# checkUrlExists (VixSrc)
# ---------------------------------------------------------------------------

async def check_url_exists(url: str) -> bool:
    """Verifica tramite HEAD se un URL VixSrc esiste. Skip con VIXSRC_SKIP_LIST_CHECK=1."""
    skip = os.getenv("VIXSRC_SKIP_LIST_CHECK", "").lower() in ("1", "true", "on", "yes", "y")
    if skip:
        logger.debug(f"[VixSrc][Check] skip attivo -> assumo esistente: {url}")
        return True

    head_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://vixsrc.to/",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.head(url, headers=head_headers)
        if resp.status_code in range(200, 400):
            logger.info(f"[VixSrc][Check] OK ({resp.status_code}) -> {url}")
            return True
        if resp.status_code == 404:
            logger.info(f"[VixSrc][Check] 404 -> {url}")
            return False
        logger.warning(f"[VixSrc][Check] Status bloccato ({resp.status_code}), assumo esistente")
        return True
    except Exception as e:
        logger.warning(f"[VixSrc][Check] errore rete per {url}: {e} -> assumo esistente")
        return True


# ---------------------------------------------------------------------------
# build_proxy_url
# ---------------------------------------------------------------------------

def build_proxy_url(m3u8_url: str, addon_base_url: str) -> str:
    base    = addon_base_url.rstrip("/")
    encoded = quote(m3u8_url, safe="")
    return f"{base}/proxy/manifest.m3u8?url={encoded}"


# ---------------------------------------------------------------------------
# Helpers URL
# ---------------------------------------------------------------------------

def _ensure_m3u8(raw: str) -> str:
    """Aggiunge .m3u8 al path /playlist/<id> se mancante."""
    try:
        if "/playlist/" not in raw:
            return raw
        from urllib.parse import urlparse, urlunparse
        p = urlparse(raw)
        parts = p.path.split("/")
        idx = parts.index("playlist") if "playlist" in parts else -1
        if idx == -1 or idx == len(parts) - 1:
            return raw
        leaf = parts[idx + 1]
        if "." in leaf or leaf.endswith(".m3u8"):
            return raw
        parts[idx + 1] = leaf + ".m3u8"
        return urlunparse(p._replace(path="/".join(parts)))
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Step 1: ricava URL embed VixCloud dalla pagina VixSrc
# ---------------------------------------------------------------------------

async def _get_vixcloud_embed(vixsrc_url: str) -> Optional[str]:
    parsed = urlparse(vixsrc_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path   = parsed.path.rstrip("/")

    api_path = path.replace("/tv/", "/api/tv/", 1).replace("/movie/", "/api/movie/", 1)
    api_url  = f"{origin}{api_path}"
    logger.info(f"🔌 VixSrc API: {api_url}")

    api_headers = {
        **_HEADERS,
        "Accept": "application/json",
        "Referer": f"{origin}/",
        "Origin": origin,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(api_url, headers=api_headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    src = data.get("src") or data.get("iframe") or data.get("embed")
                    if src:
                        embed_url = src if src.startswith("http") else f"{origin}{src}"
                        logger.info(f"🔗 VixCloud embed (API): {embed_url[:100]}")
                        return embed_url
                except Exception as e:
                    logger.debug(f"[vixsrc] API JSON parse error: {e}")
            else:
                logger.warning(f"[vixsrc] API status {resp.status_code} per {api_url}")
        except Exception as e:
            logger.warning(f"[vixsrc] API request error: {e}")

        logger.info(f"[vixsrc] Fallback Inertia: {vixsrc_url}")
        inertia_headers = {**_HEADERS, "x-inertia": "true", "Referer": f"{origin}/"}

        try:
            page_resp = await client.get(vixsrc_url, headers=inertia_headers)
            page_resp.raise_for_status()
            ct = page_resp.headers.get("content-type", "")

            if "application/json" in ct:
                try:
                    idata = page_resp.json()
                    props = idata.get("props", {})
                    src = props.get("src") or props.get("iframe") or props.get("embed")
                    if isinstance(props.get("stream"), dict):
                        src = src or props["stream"].get("src")
                    if src:
                        embed_url = src if src.startswith("http") else f"{origin}{src}"
                        logger.info(f"🔗 VixCloud embed (Inertia JSON): {embed_url[:100]}")
                        return embed_url
                except Exception as e:
                    logger.debug(f"[vixsrc] Inertia JSON parse error: {e}")
            else:
                html = page_resp.text
                m = re.search(r'id="app"[^>]+data-page="([^"]+)"', html) or \
                    re.search(r"data-page='([^']+)'", html)
                if m:
                    try:
                        page_data = json.loads(m.group(1).replace("&quot;", '"'))
                        props = page_data.get("props", {})
                        src = props.get("src") or props.get("iframe") or props.get("embed")
                        if isinstance(props.get("stream"), dict):
                            src = src or props["stream"].get("src")
                        if src:
                            embed_url = src if src.startswith("http") else f"{origin}{src}"
                            logger.info(f"🔗 VixCloud embed (data-page): {embed_url[:100]}")
                            return embed_url
                    except Exception as e:
                        logger.debug(f"[vixsrc] data-page parse error: {e}")

                mi = _IFRAME_VIXCLOUD_RE.search(html)
                if mi:
                    embed_url = mi.group(1)
                    if embed_url.startswith("//"):
                        embed_url = "https:" + embed_url
                    logger.info(f"🔗 VixCloud embed (iframe regex): {embed_url[:100]}")
                    return embed_url

                if "masterPlaylist" in html:
                    logger.info("🔗 masterPlaylist nella pagina VixSrc diretta")
                    return vixsrc_url

        except httpx.HTTPStatusError as e:
            logger.warning(f"[vixsrc] Fallback HTTP {e.response.status_code}")
        except Exception as e:
            logger.warning(f"[vixsrc] Fallback request error: {e}")

    logger.warning(f"⚠️ Nessun embed VixCloud per {vixsrc_url}")
    return None


# ---------------------------------------------------------------------------
# Step 2: estrai M3U8 dall'embed VixCloud
# ---------------------------------------------------------------------------

def _parse_window_vars(script: str) -> Optional[dict]:
    raw    = script.replace("\n", "\t")
    key_re = re.compile(r"window\.(\w+)\s*=\s*")
    keys   = key_re.findall(raw)
    parts  = re.split(r"window\.\w+\s*=\s*", raw)[1:]
    if not keys or len(keys) != len(parts):
        return None
    objs = []
    for key, part in zip(keys, parts):
        cleaned = re.sub(r";", "", part)
        cleaned = re.sub(r"([{\[,])\s*(\w+)\s*:", r'\1 "\2":', cleaned)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned).strip().replace("'", '"')
        objs.append(f'"{key}": {cleaned}')
    try:
        return json.loads("{\n" + ",\n".join(objs) + "\n}")
    except json.JSONDecodeError:
        return None


async def _extract_m3u8_from_embed(embed_url: str, referer: str) -> Optional[str]:
    p      = urlparse(referer)
    origin = f"{p.scheme}://{p.netloc}"
    hdrs   = {**_HEADERS, "Referer": referer, "Origin": origin}

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(embed_url, headers=hdrs)
        resp.raise_for_status()
    html = resp.text

    script_tag: Optional[str] = None
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
        c = m.group(1)
        if re.search(r"['\"]token['\"]\s*:", c) and re.search(r"['\"]expires['\"]\s*:", c):
            script_tag = c
            break
    if not script_tag:
        for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
            if "masterPlaylist" in m.group(1):
                script_tag = m.group(1)
                break

    if not script_tag:
        logger.warning(f"[vixcloud] Nessuno script token/masterPlaylist in {embed_url[:80]}")
        return None

    token_m   = re.search(r"['\"]token['\"]\s*:\s*['\"]([\w-]+)['\"]", script_tag)
    expires_m = re.search(r"['\"]expires['\"]\s*:\s*['\"]?(\d+)['\"]?", script_tag)
    url_m     = re.search(r"url\s*:\s*['\"]([^'\"]+)['\"]", script_tag)

    if token_m and expires_m and url_m:
        token      = token_m.group(1)
        expires    = expires_m.group(1)
        server_url = url_m.group(1)

        server_url = _ensure_m3u8(server_url)
        before_q   = server_url.split("?")[0]
        if not before_q.lower().endswith(".m3u8"):
            server_url = before_q.rstrip("/") + ".m3u8"

        had_b  = "b=1" in url_m.group(1)
        params: List[str] = []
        if had_b:
            params.append("b=1")
        params.append(f"token={quote(token, safe='')}")
        params.append(f"expires={quote(expires, safe='')}")

        if re.search(r"['\"]?canPlayFHD['\"]?\s*[=:]\s*true", script_tag):
            params.append("h=1")
            logger.info("[vixcloud] canPlayFHD=true → h=1")

        final_url = server_url + "?" + "&".join(params)
        logger.info(f"✅ VixCloud M3U8 (token): {final_url[:120]}")
        return final_url

    parsed = _parse_window_vars(script_tag)
    if parsed:
        master = parsed.get("masterPlaylist")
        if master:
            base_url   = master.get("url", "")
            params_obj = master.get("params") or {}
            token   = str(params_obj.get("token", ""))
            expires = str(params_obj.get("expires", ""))

            base_url = _ensure_m3u8(base_url)
            before_q = base_url.split("?")[0]
            if not before_q.lower().endswith(".m3u8"):
                base_url = before_q.rstrip("/") + ".m3u8"

            sep       = "&" if "?" in base_url else "?"
            final_url = f"{base_url}{sep}token={quote(token, safe='')}&expires={quote(expires, safe='')}"
            if parsed.get("canPlayFHD") is True:
                final_url += "&h=1"
                logger.info("[vixcloud] canPlayFHD=true (masterPlaylist) → h=1")

            logger.info(f"✅ VixCloud M3U8 (masterPlaylist): {final_url[:120]}")
            return final_url

    m_fb = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', script_tag)
    if m_fb:
        logger.info(f"[vixcloud] Fallback regex URL: {m_fb.group(1)[:80]}")
        return m_fb.group(1)

    logger.warning(f"[vixcloud] Impossibile estrarre M3U8 da {embed_url[:80]}")
    return None


async def extract_m3u8(page_url: str) -> Optional[str]:
    try:
        embed_url = await _get_vixcloud_embed(page_url)
        if not embed_url:
            return None
        return await _extract_m3u8_from_embed(embed_url, page_url)
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTP {e.response.status_code} per {e.request.url}")
    except httpx.RequestError as e:
        logger.error(f"❌ Request error: {e}")
    except Exception as e:
        logger.error(f"❌ extract_m3u8 errore inatteso: {e}")
    return None


# ---------------------------------------------------------------------------
# Resolver VixCloud (wrappato per asyncio.gather)
# ---------------------------------------------------------------------------

async def _resolve_vixcloud(
    page_url: str,
    content_label: str,
    addon_base_url: str,
    is_series: bool,
    season: Optional[str],
    episode: Optional[str],
    ep_title_task,
) -> Optional[Dict]:
    """Wrapper async per VixSrc/VixCloud compatibile con asyncio.gather()."""
    try:
        exists = await check_url_exists(page_url)
        if not exists:
            logger.warning(f"⚠️ Contenuto non trovato su VixSrc: {page_url}")
            return None

        vixcloud_m3u8 = await extract_m3u8(page_url)

        if is_series and ep_title_task:
            label = (await ep_title_task) or content_label
        else:
            label = content_label

        if not (isinstance(vixcloud_m3u8, str) and vixcloud_m3u8):
            logger.error(f"❌ VixCloud: impossibile estrarre M3U8 per {page_url}")
            return None

        stream_url = build_proxy_url(vixcloud_m3u8, addon_base_url)
        logger.info(f"✅ VixCloud stream pronto: {stream_url[:80]}...")
        return {
            "name": "UFO\n🇮🇹 Streaming Community",
            "title": label,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": True,
                "bingeGroup": "ufo-vixcloud",
            },
        }
    except Exception as e:
        logger.error(f"❌ _resolve_vixcloud error: {e}")
        return None


# ---------------------------------------------------------------------------
# get_streams: entry point Stremio — multi-source
# ---------------------------------------------------------------------------

async def get_streams(
    stremio_id: str,
    content_type: str,
    addon_base_url: str = "",
) -> Dict:
    """
    Parametri:
      stremio_id      es. "tt1234567" oppure "tt1234567:2:3"
      content_type    "movie" | "series"
      addon_base_url  base URL dell'addon per costruire URL proxy interno

    Lancia VixCloud e VidXgo in parallelo con asyncio.gather().
    Restituisce tutti gli stream validi trovati.
    """
    result: Dict = {"streams": []}
    try:
        parts      = stremio_id.split(":")
        content_id = parts[0]
        season     = parts[1] if len(parts) > 1 else None
        episode    = parts[2] if len(parts) > 2 else None
        is_series  = content_type == "series" and bool(season) and bool(episode)

        tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
        if not tmdb_id:
            logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
            return result

        content_label = tmdb_title or ("Serie TV" if is_series else "Film")

        # IMDB ID: se stremio_id già inizia con "tt" lo usiamo direttamente,
        # altrimenti content_id è un TMDB id numerico e VidXgo non può usarlo.
        imdb_id: Optional[str] = content_id if content_id.startswith("tt") else None

        if is_series:
            page_url      = f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            ep_title_task = asyncio.create_task(get_episode_title(tmdb_id, season, episode))
        else:
            page_url      = f"{SC_DOMAIN}/movie/{tmdb_id}/"
            ep_title_task = None

        logger.info(f"🎬 VixSrc page: {page_url}")

        # --- Coroutine VixCloud ---
        vixcloud_coro = _resolve_vixcloud(
            page_url, content_label, addon_base_url,
            is_series, season, episode, ep_title_task
        )

        # --- Coroutine VidXgo ---
        if imdb_id:
            vidxgo_coro = resolve_vidxgo(
                imdb_id, content_label, content_type,
                season, episode, addon_base_url
            )
        else:
            async def _noop_vidxgo():
                return None
            vidxgo_coro = _noop_vidxgo()

        # --- Lancia entrambi in parallelo ---
        vixcloud_stream, vidxgo_stream = await asyncio.gather(
            vixcloud_coro,
            vidxgo_coro,
            return_exceptions=True,
        )

        # VixCloud
        if isinstance(vixcloud_stream, Exception):
            logger.error(f"❌ VixCloud exception: {vixcloud_stream}")
        elif isinstance(vixcloud_stream, dict):
            result["streams"].append(vixcloud_stream)

        # VidXgo
        if isinstance(vidxgo_stream, Exception):
            logger.error(f"❌ VidXgo exception: {vidxgo_stream}")
        elif isinstance(vidxgo_stream, dict):
            result["streams"].append(vidxgo_stream)

    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")

    return result
