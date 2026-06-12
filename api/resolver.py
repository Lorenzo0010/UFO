"""
resolver.py — estrazione M3U8 da VixSrc/VixCloud via fetch HTTP puro.

Logica portata da streamvix/src/extractor.ts (getDirectStream):
  1. GET /api/tv/<tmdb>/<s>/<e> oppure /api/movie/<tmdb>  → JSON con campo "src"
  2. GET sull'URL embed restituito da "src" (con header Referer = pagina VixSrc)
  3. Trova lo script inline con token/expires/masterPlaylist
  4. Parsa e costruisce URL HLS finale
  5. Aggiunge &h=1 se canPlayFHD === true

Nessun Playwright, nessun browser headless. Puro httpx async.
"""

import asyncio
import json
import logging
import re
from typing import Dict, Optional
from urllib.parse import quote, urlparse

import httpx

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW, USER_AGENT
from .tmdb import get_tmdb_info, get_episode_title

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
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
# Step 1 — ricava l'URL embed VixCloud dalla pagina VixSrc tramite API Inertia
# ---------------------------------------------------------------------------

async def _get_vixcloud_embed(vixsrc_url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    VixSrc è una SPA Inertia.js: l'iframe NON compare nell'HTML statico.
    Replicando streamvix/extractor.ts getDirectStream():
      - Chiama /api/tv/<tmdb>/<s>/<e> o /api/movie/<tmdb> per ottenere {"src": "/embed/..."}
      - Costruisce l'URL embed assoluto e lo restituisce
    Fallback: se l'API non ha un campo "src", fetcha la pagina con x-inertia header
    e cerca l'iframe nel JSON Inertia iniettato nel div#app[data-page].
    """
    parsed = urlparse(vixsrc_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    # Costruisci URL API: /tv/615/6/1 → /api/tv/615/6/1
    api_path = path.replace("/tv/", "/api/tv/", 1).replace("/movie/", "/api/movie/", 1)
    api_url = f"{origin}{api_path}"

    logger.info(f"🔌 VixSrc API: {api_url}")

    api_headers = {
        **_HEADERS,
        "Accept": "application/json",
        "Referer": f"{origin}/",
        "Origin": origin,
    }

    try:
        api_resp = await client.get(api_url, headers=api_headers, follow_redirects=True)
        if api_resp.status_code == 200:
            try:
                data = api_resp.json()
                src = data.get("src") or data.get("iframe") or data.get("embed")
                if src:
                    embed_url = src if src.startswith("http") else f"{origin}{src}"
                    logger.info(f"🔗 VixCloud embed (API): {embed_url[:100]}")
                    return embed_url
                else:
                    logger.debug(f"[vixsrc] API risposta senza 'src': {str(data)[:200]}")
            except Exception as e:
                logger.debug(f"[vixsrc] API JSON parse error: {e}")
        else:
            logger.warning(f"[vixsrc] API status {api_resp.status_code} per {api_url}")
    except httpx.RequestError as e:
        logger.warning(f"[vixsrc] API request error: {e}")

    # Fallback: fetch pagina con x-inertia header, cerca iframe nel JSON data-page
    logger.info(f"[vixsrc] Fallback: fetch pagina Inertia {vixsrc_url}")
    inertia_headers = {
        **_HEADERS,
        "x-inertia": "true",
        "Referer": f"{origin}/",
    }

    try:
        page_resp = await client.get(vixsrc_url, headers=inertia_headers, follow_redirects=True)
        page_resp.raise_for_status()
        content_type = page_resp.headers.get("content-type", "")

        if "application/json" in content_type:
            # Risposta Inertia JSON
            try:
                inertia_data = page_resp.json()
                props = inertia_data.get("props", {})
                src = (props.get("src") or props.get("iframe") or props.get("embed")
                       or props.get("stream", {}).get("src") if isinstance(props.get("stream"), dict) else None)
                if src:
                    embed_url = src if src.startswith("http") else f"{origin}{src}"
                    logger.info(f"🔗 VixCloud embed (Inertia JSON): {embed_url[:100]}")
                    return embed_url
            except Exception as e:
                logger.debug(f"[vixsrc] Inertia JSON parse error: {e}")
        else:
            html = page_resp.text
            # Cerca nell'attributo data-page del div#app
            m = re.search(r'id="app"[^>]+data-page="([^"]+)"', html)
            if not m:
                m = re.search(r"data-page='([^']+)'", html)
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

            # Ultimo fallback: cerca <iframe src="...vixcloud..."> nell'HTML
            mi = re.search(r'<iframe[^>]+src=["\']((?:https?:)?//[^"\']*vixcloud[^"\']*)["\']', html, re.IGNORECASE)
            if mi:
                embed_url = mi.group(1)
                if embed_url.startswith("//"):
                    embed_url = "https:" + embed_url
                logger.info(f"🔗 VixCloud embed (iframe regex): {embed_url[:100]}")
                return embed_url

            if "masterPlaylist" in html:
                logger.info("🔗 masterPlaylist trovato direttamente nella pagina VixSrc")
                return vixsrc_url

    except httpx.HTTPStatusError as e:
        logger.warning(f"[vixsrc] Fallback HTTP {e.response.status_code} per {vixsrc_url}")
    except httpx.RequestError as e:
        logger.warning(f"[vixsrc] Fallback request error: {e}")

    logger.warning(f"⚠️ Nessun embed VixCloud trovato per {vixsrc_url}")
    return None


# ---------------------------------------------------------------------------
# Step 2 — estrai M3U8 dall'embed VixCloud
# ---------------------------------------------------------------------------

def _sanitise_and_parse_window_vars(script: str) -> Optional[dict]:
    """
    Replica getSanitisedScript() di vixcloud.ts:
    split su ogni `window.VAR = ` e ricostruisce un JSON aggregato.
    """
    raw = script.replace("\n", "\t")

    key_re = re.compile(r"window\.(\w+)\s*=\s*")
    keys = key_re.findall(raw)
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


async def _extract_m3u8_from_embed(embed_url: str, referer: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Porta diretta di VixCloudHlsExtractor.extract() da extractor.ts.
    Fetcha l'embed con Referer = pagina VixSrc originale.
    """
    headers = {
        **_HEADERS,
        "Referer": referer,
        "Origin": f"{urlparse(referer).scheme}://{urlparse(referer).netloc}",
    }
    resp = await client.get(embed_url, headers=headers, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Cerca il <script> che contiene token/expires (pattern streamvix)
    script_tag = None
    for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
        content = m.group(1)
        if "'token':" in content and "'expires':" in content:
            script_tag = content
            break
    if not script_tag:
        # Fallback: script con masterPlaylist
        for m in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
            if "masterPlaylist" in m.group(1):
                script_tag = m.group(1)
                break

    if not script_tag:
        logger.warning(f"[vixcloud] Nessuno script con token/masterPlaylist in {embed_url[:80]}")
        return None

    # --- Pattern streamvix: 'token': 'xxx', 'expires': 'xxx', url: 'xxx' ---
    token_m = re.search(r"'token'\s*:\s*'(\w+)'", script_tag)
    expires_m = re.search(r"'expires'\s*:\s*'(\d+)'", script_tag)
    url_m = re.search(r"url\s*:\s*'([^']+)'", script_tag)

    if token_m and expires_m and url_m:
        token = token_m.group(1)
        expires = expires_m.group(1)
        server_url = url_m.group(1)

        # Assicura .m3u8 nel path
        before_q = server_url.split("?")[0]
        if not re.search(r"\.m3u8$", before_q, re.IGNORECASE):
            server_url = before_q.rstrip("/") + ".m3u8"

        # Componi query: b (se presente), token, expires, h (se FHD)
        parsed_srv = urlparse(server_url)
        # cerca b=1 nell'URL originale
        had_b = "b=1" in (url_m.group(1))

        params = []
        if had_b:
            params.append("b=1")
        params.append(f"token={quote(token, safe='')}")
        params.append(f"expires={quote(expires, safe='')}")

        fhd = bool(re.search(r"window\.canPlayFHD\s*=\s*true", script_tag))
        if fhd:
            params.append("h=1")

        final_url = server_url + "?" + "&".join(params)
        logger.info(f"✅ VixCloud M3U8 (token pattern): {final_url[:120]}")
        return final_url

    # Fallback: pattern masterPlaylist (vecchio embed)
    parsed = _sanitise_and_parse_window_vars(script_tag)
    if parsed:
        master = parsed.get("masterPlaylist")
        if master:
            base_url: str = master.get("url", "")
            params_obj = master.get("params", {}) or {}
            token = params_obj.get("token", "")
            expires = params_obj.get("expires", "")

            before_query = base_url.split("?")[0]
            if not re.search(r"\.m3u8$", before_query, re.IGNORECASE):
                base_url = before_query.rstrip("/") + ".m3u8"

            param_str = f"token={quote(str(token), safe='')}&expires={quote(str(expires), safe='')}"
            separator = "&" if "?" in base_url else "?"
            final_url = base_url + separator + param_str

            if parsed.get("canPlayFHD") is True:
                final_url += "&h=1"

            logger.info(f"✅ VixCloud M3U8 (masterPlaylist): {final_url[:120]}")
            return final_url

    # Ultimo fallback: regex diretta su URL .m3u8
    m = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', script_tag)
    if m:
        logger.info(f"[vixcloud] Fallback regex URL: {m.group(1)[:80]}")
        return m.group(1)

    logger.warning(f"[vixcloud] Impossibile estrarre M3U8 da {embed_url[:80]}")
    return None


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
            return await _extract_m3u8_from_embed(embed_url, page_url, client)
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
