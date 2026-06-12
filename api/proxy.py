"""
proxy.py — Proxy HLS interno per UFO.

Fix applicati:
  1. Aggiunta route HEAD /proxy/manifest.m3u8 → Stremio non riceve più 405
  2. _rewrite_manifest ora riscrive anche URI in #EXT-X-KEY → enc.key non
     viene più cercato localmente ma proxiato da vixsrc.to
"""

import logging
import re
from urllib.parse import quote, urljoin, urlparse

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
    - Righe URL (segmenti, variant playlist)
    - URI="..." in qualsiasi direttiva (#EXT-X-MAP, #EXT-X-KEY, ecc.)
    """
    lines = content.splitlines(keepends=True)
    rewritten = []

    def proxify_uri(raw: str) -> str:
        abs_url = _make_absolute(raw, original_url)
        parsed = urlparse(abs_url)
        if parsed.path.endswith(".m3u8") or ".m3u8?" in parsed.path:
            return f"{proxy_base}/manifest.m3u8?url={quote(abs_url, safe='')}"
        return f"{proxy_base}/segment?url={quote(abs_url, safe='')}"

    for line in lines:
        stripped = line.strip()

        if not stripped:
            rewritten.append(line)
            continue

        if stripped.startswith("#"):
            # Riscrive tutti gli URI="..." nelle direttive HLS
            # Copre: #EXT-X-KEY, #EXT-X-MAP, #EXT-X-MEDIA, ecc.
            if 'URI="' in stripped:
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{proxify_uri(m.group(1))}"',
                    line,
                )
            rewritten.append(line)
            continue

        # Riga URL (segmento o variant playlist)
        rewritten.append(proxify_uri(stripped) + "\n")

    return "".join(rewritten)


def _make_absolute(url: str, base: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base, url)


def _proxy_base(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/proxy"


# ── HEAD: risponde 200 con gli stessi header del GET ─────────────────────────
@router.head("/manifest.m3u8")
async def proxy_manifest_head(url: str, request: Request):
    """Risponde alle richieste HEAD di Stremio senza fetchare il manifest."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")
    return Response(
        content=b"",
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


# ── GET manifest ──────────────────────────────────────────────────────────────
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

        rewritten = _rewrite_manifest(resp.text, url, _proxy_base(request))

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


# ── GET segmento ──────────────────────────────────────────────────────────────
@router.get("/segment")
async def proxy_segment(url: str):
    """Proxia un singolo segmento media (.ts, .aac, chiave AES, ecc.) in streaming."""
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
