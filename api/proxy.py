"""
proxy.py — Proxy HLS interno per UFO.

Gestisce manifest M3U8 e segmenti .ts iniettando i giusti header
(Referer, Origin, User-Agent) verso vixsrc.to, senza dipendenze esterne.

Endpoint:
  GET /proxy/manifest.m3u8?url=<encoded_m3u8_url>
  GET /proxy/segment?url=<encoded_segment_url>

Il manifest viene riscritto: ogni URL di segmento/variant viene
sostituito con un URL che punta a /proxy/segment?url=...
così tutti i chunk passano dal proxy e ricevono i giusti header.
"""

import logging
import re
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .config import USER_AGENT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

_PROXY_HEADERS = {
    "User-Agent": USER_AGENT or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Referer": "https://vixsrc.to/",
    "Origin": "https://vixsrc.to",
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
    "Connection": "keep-alive",
}

_TIMEOUT = httpx.Timeout(30.0)
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    return _client


async def close_proxy_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _rewrite_manifest(content: str, original_url: str, proxy_base: str) -> str:
    """
    Riscrive un manifest M3U8 sostituendo tutti gli URL con URL proxy.
    Gestisce sia master playlist (riferimenti a .m3u8 di qualità)
    sia media playlist (riferimenti a segmenti .ts / .aac / ecc.)
    """
    lines = content.splitlines(keepends=True)
    rewritten = []

    for line in lines:
        stripped = line.strip()

        # Salta commenti e righe vuote
        if not stripped or stripped.startswith("#"):
            # Gestisci URI inline nelle direttive EXT-X-MAP e simili
            def replace_uri(m):
                raw = m.group(1)
                abs_url = _make_absolute(raw, original_url)
                return f'URI="{proxy_base}/segment?url={quote(abs_url, safe="")}"]'

            # EXT-X-MAP:URI="..."
            if 'URI="' in stripped:
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{proxy_base}/segment?url={quote(_make_absolute(m.group(1), original_url), safe="")}"',
                    line
                )
            rewritten.append(line)
            continue

        # È un URL (segmento o variant playlist)
        abs_url = _make_absolute(stripped, original_url)
        parsed = urlparse(abs_url)

        if parsed.path.endswith(".m3u8") or ".m3u8?" in parsed.path:
            # Variant playlist → punta a /proxy/manifest.m3u8?url=...
            proxied = f"{proxy_base}/manifest.m3u8?url={quote(abs_url, safe='')}"
        else:
            # Segmento media → punta a /proxy/segment?url=...
            proxied = f"{proxy_base}/segment?url={quote(abs_url, safe='')}"

        rewritten.append(proxied + "\n")

    return "".join(rewritten)


def _make_absolute(url: str, base: str) -> str:
    """Converte un URL relativo in assoluto usando base come riferimento."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base, url)


def _proxy_base(request: Request) -> str:
    """Restituisce la base URL del proxy (es. http://192.168.1.77:8080/proxy)."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/proxy"


@router.get("/manifest.m3u8")
async def proxy_manifest(url: str, request: Request):
    """Fetcha e riscrive un manifest M3U8."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")

    logger.debug(f"[proxy] manifest: {url[:100]}")
    client = get_client()

    try:
        resp = await client.get(url, headers=_PROXY_HEADERS)
        if resp.status_code != 200:
            logger.warning(f"[proxy] manifest HTTP {resp.status_code} per {url[:80]}")
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        content = resp.text
        rewritten = _rewrite_manifest(content, url, _proxy_base(request))

        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
            },
        )
    except httpx.RequestError as e:
        logger.error(f"[proxy] manifest request error: {e}")
        raise HTTPException(status_code=502, detail="Upstream non raggiungibile")


@router.get("/segment")
async def proxy_segment(url: str):
    """Proxia un singolo segmento media (.ts, .aac, ecc.) in streaming."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")

    logger.debug(f"[proxy] segment: {url[:100]}")
    client = get_client()

    try:
        req = client.build_request("GET", url, headers=_PROXY_HEADERS)
        resp = await client.send(req, stream=True)

        if resp.status_code not in (200, 206):
            await resp.aclose()
            logger.warning(f"[proxy] segment HTTP {resp.status_code} per {url[:80]}")
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        content_type = resp.headers.get("content-type", "video/MP2T")

        async def stream_chunks():
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                yield chunk
            await resp.aclose()

        return StreamingResponse(
            stream_chunks(),
            status_code=resp.status_code,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
            },
        )
    except httpx.RequestError as e:
        logger.error(f"[proxy] segment request error: {e}")
        raise HTTPException(status_code=502, detail="Upstream non raggiungibile")
