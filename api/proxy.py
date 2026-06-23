"""
proxy.py — Proxy HLS interno per UFO.

Fix applicati:
  1. HEAD /proxy/manifest.m3u8 → risponde 200 (Stremio non riceve più 405)
  2. _rewrite_manifest riscrive URI in #EXT-X-KEY, #EXT-X-MAP, ecc.
  3. /proxy/segment rileva sub-playlist M3U8 (anche senza .m3u8 nell'URL)
     e le riscrive prima di restituirle → enc.key proxiato correttamente
  4. Supporto header dinamici via ?headers=<base64-JSON>:
     usato da VidXgo (e altri provider futuri) per passare Origin/Referer/UA
     specifici ai segmenti CDN che richiedono header particolari.
  5. /proxy/mp4.m3u8 genera un manifest M3U8 sintetico per URL MP4 diretti
     (es. Mixdrop) — il player riceve un HLS valido invece di un link raw.
  6. /proxy/segment usa streaming vero (iter_bytes) per file MP4 grandi:
     non carica mai l'intero body in memoria, evita timeout e corruzione.
  7. Fix StreamConsumed: il context manager dello stream viene mantenuto
     vivo per tutta la durata della StreamingResponse tramite un generatore
     asincrono dedicato che non esce dal `async with` prima del completamento.
"""

import base64
import json
import logging
import re
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .config import USER_AGENT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

_PROXY_HEADERS_VIXCLOUD = {
    "User-Agent": USER_AGENT or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Referer": "https://vixsrc.to/",
    "Origin": "https://vixsrc.to",
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
    "Connection": "keep-alive",
}

_TIMEOUT = httpx.Timeout(30.0)
_client: httpx.AsyncClient | None = None

_M3U8_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}

# Chunk size per lo streaming dei segmenti binari (512 KB)
_CHUNK_SIZE = 512 * 1024


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


def _decode_headers_param(headers_b64: str | None) -> dict:
    """
    Decodifica il parametro ?headers=<base64-JSON> opzionale.
    Restituisce un dict vuoto se assente o malformato.
    """
    if not headers_b64:
        return {}
    try:
        decoded = base64.b64decode(headers_b64 + "==").decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def _build_headers(custom: dict) -> dict:
    """
    Unisce gli header di default VixCloud con quelli custom.
    Gli header custom hanno la precedenza (permettono override di UA, Referer, Origin).
    """
    merged = {**_PROXY_HEADERS_VIXCLOUD}
    merged.update(custom)
    return merged


def _is_m3u8_content_type(content_type: str) -> bool:
    """Controlla solo il Content-Type senza leggere il body."""
    ct = content_type.split(";")[0].strip().lower()
    return ct in _M3U8_CONTENT_TYPES


def _is_m3u8_peek(content_type: str, first_bytes: bytes) -> bool:
    """Rileva M3U8 dal Content-Type o dai primi byte (senza consumare tutto il body)."""
    if _is_m3u8_content_type(content_type):
        return True
    return first_bytes.lstrip()[:7] == b"#EXTM3U"


def _rewrite_manifest(content: str, original_url: str, proxy_base: str, headers_b64: str | None = None) -> str:
    """
    Riscrive un manifest M3U8 sostituendo tutti gli URL con URL proxy.
    Propaga il parametro headers_b64 agli URL riscritti se presente.
    """
    lines = content.splitlines(keepends=True)
    rewritten = []

    def proxify_uri(raw: str) -> str:
        abs_url = _make_absolute(raw, original_url)
        parsed = urlparse(abs_url)
        h_param = f"&headers={quote(headers_b64, safe='')}" if headers_b64 else ""
        if parsed.path.endswith(".m3u8") or ".m3u8?" in parsed.path:
            return f"{proxy_base}/manifest.m3u8?url={quote(abs_url, safe='')}{h_param}"
        return f"{proxy_base}/segment?url={quote(abs_url, safe='')}{h_param}"

    for line in lines:
        stripped = line.strip()

        if not stripped:
            rewritten.append(line)
            continue

        if stripped.startswith("#"):
            if 'URI="' in stripped:
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{proxify_uri(m.group(1))}"',
                    line,
                )
            rewritten.append(line)
            continue

        rewritten.append(proxify_uri(stripped) + "\n")

    return "".join(rewritten)


def _make_absolute(url: str, base: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base, url)


def _proxy_base(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/proxy"


def encode_headers_b64(headers: dict) -> str:
    """Serializza un dict di header in base64-JSON per il param ?headers=."""
    return base64.b64encode(json.dumps(headers).encode()).decode().rstrip("=")


# ── HEAD manifest ─────────────────────────────────────────────────────────────
@router.head("/manifest.m3u8")
async def proxy_manifest_head(url: str, request: Request, headers: str | None = None):
    """Risponde alle richieste HEAD di Stremio senza fetchare il manifest."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")
    return Response(
        content=b"",
        media_type="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
    )


# ── GET manifest ──────────────────────────────────────────────────────────────
@router.get("/manifest.m3u8")
async def proxy_manifest(url: str, request: Request, headers: str | None = None):
    """Fetcha e riscrive un manifest M3U8 (master o media playlist)."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")

    logger.debug(f"[proxy] manifest: {url[:100]}")
    client = get_client()
    custom_headers = _decode_headers_param(headers)
    effective_headers = _build_headers(custom_headers)

    try:
        resp = await client.get(url, headers=effective_headers)
        if resp.status_code != 200:
            logger.warning(f"[proxy] manifest HTTP {resp.status_code} per {url[:80]}")
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        rewritten = _rewrite_manifest(resp.text, url, _proxy_base(request), headers)

        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )
    except httpx.RequestError as e:
        logger.error(f"[proxy] manifest request error: {e}")
        raise HTTPException(status_code=502, detail="Upstream non raggiungibile")


# ── HEAD mp4.m3u8 ─────────────────────────────────────────────────────────────
@router.head("/mp4.m3u8")
async def proxy_mp4_manifest_head(url: str, request: Request, headers: str | None = None):
    """Risponde alle richieste HEAD per il manifest M3U8 sintetico MP4."""
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")
    return Response(
        content=b"",
        media_type="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
    )


# ── GET mp4.m3u8 ──────────────────────────────────────────────────────────────
@router.get("/mp4.m3u8")
async def proxy_mp4_manifest(url: str, request: Request, headers: str | None = None):
    """
    Genera un manifest M3U8 sintetico per un URL MP4 diretto (es. Mixdrop).

    Il manifest contiene un singolo segmento che punta all'MP4 tramite
    /proxy/segment, propagando gli header di autenticazione necessari.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")

    logger.info(f"[proxy] mp4.m3u8 sintetico per: {url[:100]}")

    base = _proxy_base(request)
    h_param = f"&headers={quote(headers, safe='')}" if headers else ""
    segment_url = f"{base}/segment?url={quote(url, safe='')}{h_param}"

    manifest = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:7200\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:7200.0,\n"
        f"{segment_url}\n"
        "#EXT-X-ENDLIST\n"
    )

    return Response(
        content=manifest,
        media_type="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
    )


# ── GET segmento ──────────────────────────────────────────────────────────────
@router.get("/segment")
async def proxy_segment(url: str, request: Request, headers: str | None = None):
    """
    Proxia segmenti media (.ts, .aac, MP4, chiave AES, ecc.).

    Usa streaming vero (iter_bytes) per non caricare mai l'intero body
    in memoria — fondamentale per file MP4 di grandi dimensioni.

    Se la risposta upstream è un M3U8 (sub-playlist senza estensione .m3u8),
    la legge completamente, la riscrive e la restituisce come testo.
    Supporta header personalizzati via ?headers=<base64-JSON>.

    FIX StreamConsumed: il generatore asincrono `_stream_body` mantiene
    aperto il context manager httpx per tutta la durata dello streaming,
    evitando che venga chiuso prima che la StreamingResponse finisca di
    inviare i dati al client.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Parametro 'url' mancante")

    logger.debug(f"[proxy] segment: {url[:100]}")
    custom_headers = _decode_headers_param(headers)
    effective_headers = _build_headers(custom_headers)

    # Usiamo un client dedicato per-segmento con timeout più lungo.
    # NON usiamo il client globale get_client() perché lo stream deve
    # rimanere aperto oltre il return di questa funzione, e un client
    # condiviso potrebbe essere chiuso da altre richieste concorrenti.
    segment_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
    )

    try:
        # Invia la richiesta e ottieni gli header senza consumare il body
        req = segment_client.build_request("GET", url, headers=effective_headers)
        resp = await segment_client.send(req, stream=True)

        if resp.status_code not in (200, 206):
            await resp.aclose()
            await segment_client.aclose()
            logger.warning(f"[proxy] segment HTTP {resp.status_code} per {url[:80]}")
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        content_type = resp.headers.get("content-type", "video/MP2T")

        # Leggi solo i primi 512 byte per il rilevamento M3U8 (senza consumare lo stream)
        first_chunk = b""
        async for chunk in resp.aiter_bytes(512):
            first_chunk = chunk
            break

        # Se è un M3U8 (sub-playlist), leggi tutto e riscrivi
        if _is_m3u8_peek(content_type, first_chunk):
            logger.debug(f"[proxy] sub-playlist rilevata, riscrittura: {url[:80]}")
            rest = await resp.aread()
            await resp.aclose()
            await segment_client.aclose()
            full_body = (first_chunk + rest).decode("utf-8", errors="replace")
            rewritten = _rewrite_manifest(full_body, url, _proxy_base(request), headers)
            return Response(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
            )

        # Altrimenti: streaming binario progressivo.
        # Il generatore mantiene vivi resp e segment_client per tutta la
        # durata della StreamingResponse — vengono chiusi solo al termine.
        extra_headers: dict[str, str] = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        }
        if cl := resp.headers.get("content-length"):
            extra_headers["Content-Length"] = cl
        if ar := resp.headers.get("accept-ranges"):
            extra_headers["Accept-Ranges"] = ar

        async def _stream_body():
            try:
                yield first_chunk
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    yield chunk
            finally:
                await resp.aclose()
                await segment_client.aclose()

        return StreamingResponse(
            _stream_body(),
            status_code=resp.status_code,
            media_type=content_type,
            headers=extra_headers,
        )

    except httpx.RequestError as e:
        await segment_client.aclose()
        logger.error(f"[proxy] segment request error: {e}")
        raise HTTPException(status_code=502, detail="Upstream non raggiungibile")
