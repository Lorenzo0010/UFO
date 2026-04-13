import logging
import re
from urllib.parse import quote, unquote, urlencode, urlparse, parse_qs

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from .config import EASYPROXY_PSW, USER_AGENT, SC_DOMAIN

logger = logging.getLogger(__name__)
router = APIRouter()

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _check_password(api_password: str):
    if EASYPROXY_PSW and api_password != EASYPROXY_PSW:
        raise HTTPException(status_code=403, detail="Forbidden")


def _rewrite_m3u8(content: str, base_url: str, referer: str) -> str:
    """
    Riscrive ogni URL nel manifest M3U8 per passare attraverso il nostro /proxy/hls/segment.
    Gestisce sia URI assoluti che relativi.
    """
    lines = content.splitlines(keepends=True)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            # Riscrive URI= dentro i tag EXT-X-KEY e EXT-X-MAP
            def replace_uri(m):
                uri = m.group(1)
                if not uri.startswith("http"):
                    parsed = urlparse(referer)
                    uri = f"{parsed.scheme}://{parsed.netloc}{uri}"
                params = {
                    "u": uri,
                    "h_user-agent": USER_AGENT,
                    "h_accept": BROWSER_HEADERS["Accept"],
                    "h_accept-language": BROWSER_HEADERS["Accept-Language"],
                    "h_referer": referer,
                }
                return f'URI="{base_url}/proxy/hls/segment?{urlencode(params)}"'
            line = re.sub(r'URI="([^"]+)"', replace_uri, line)
            out.append(line)
        elif stripped and not stripped.startswith("#"):
            # Linee URL: segmenti .ts, playlist .m3u8 figlie
            url = stripped
            if not url.startswith("http"):
                parsed = urlparse(referer)
                # URL relativo rispetto al dominio del referer
                url = f"{parsed.scheme}://{parsed.netloc}{url if url.startswith('/') else '/' + url}"
            params = {
                "u": url,
                "h_user-agent": USER_AGENT,
                "h_accept": BROWSER_HEADERS["Accept"],
                "h_accept-language": BROWSER_HEADERS["Accept-Language"],
                "h_referer": referer,
            }
            proxy_url = f"{base_url}/proxy/hls/segment?{urlencode(params)}"
            out.append(proxy_url + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)
    return "".join(out)


async def _extract_vixsrc(page_url: str) -> str:
    """
    Estrae il playlist URL da una pagina VixSrc usando httpx.
    VixSrc espone il token direttamente nell'HTML/JS della pagina.
    Ritorna l'URL della playlist M3U8.
    """
    MAX_ATTEMPTS = 3
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info(f"Attempt {attempt}/{MAX_ATTEMPTS} — {page_url}")
        try:
            headers = {**BROWSER_HEADERS, "Referer": SC_DOMAIN + "/"}
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                resp = await client.get(page_url, headers=headers)
                if resp.status_code != 200:
                    raise Exception(f"{resp.status_code}, message='Not Found', url='{page_url}'")
                html = resp.text

            # Cerca il pattern del playlist URL nell'HTML
            # VixSrc inietta qualcosa tipo: file:"https://vixsrc.to/playlist/XXXXX?token=..."
            patterns = [
                r'file["\s]*:["\s]*"(https?://[^"]+/playlist/[^"]+)"',
                r'source["\s]*:["\s]*"(https?://[^"]+/playlist/[^"]+)"',
                r'(https?://[^"\s]+/playlist/\d+[^"\s]*)',
            ]
            playlist_url = None
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    playlist_url = match.group(1)
                    break

            if not playlist_url:
                raise Exception(f"playlist URL non trovato nella pagina {page_url}")

            logger.info(f"✅ VixSrc extracted: {playlist_url[:80]}...")
            return playlist_url

        except Exception as e:
            last_error = e
            logger.error(f"❌ Non-network error attempt {attempt} — {page_url}: {e}")
            if attempt < MAX_ATTEMPTS:
                import asyncio
                await asyncio.sleep(2)

    logger.error(f"❌ VixSrc extraction failed: Final error for {page_url}: {last_error}")
    raise Exception(f"VixSrc extraction completely failed: Final error for {page_url}: {last_error}")


@router.get("/proxy/hls/manifest.m3u8")
async def proxy_manifest(
    request: Request,
    d: str = Query(..., description="URL della pagina VixSrc da proxare"),
    api_password: str = Query(default=""),
):
    _check_password(api_password)
    page_url = unquote(d)
    logger.info(f"🛸 Proxy manifest request for: {page_url}")

    try:
        playlist_url = await _extract_vixsrc(page_url)
    except Exception as e:
        logger.error(f"❌ Extraction failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    referer = page_url
    headers = {
        **BROWSER_HEADERS,
        "Referer": referer,
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(playlist_url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="VixSrc playlist error")
            content_type = resp.headers.get("content-type", "application/vnd.apple.mpegurl")
            body = resp.text
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    base_url = str(request.base_url).rstrip("/")
    rewritten = _rewrite_m3u8(body, base_url, referer)

    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/proxy/hls/segment")
async def proxy_segment(
    request: Request,
    u: str = Query(..., description="URL del segmento/risorsa da proxare"),
    api_password: str = Query(default=""),
):
    _check_password(api_password)
    target_url = unquote(u)

    # Estrae headers h_* dai query params
    extra_headers = {}
    for key, val in request.query_params.items():
        if key.startswith("h_"):
            header_name = key[2:].replace("-", "-")
            extra_headers[header_name] = unquote(val)

    headers = {**BROWSER_HEADERS, **extra_headers}

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(target_url, headers=headers)
            content_type = resp.headers.get("content-type", "application/octet-stream")

            # Se è un sotto-manifest M3U8, riscrivilo anche
            if "mpegurl" in content_type or target_url.endswith(".m3u8"):
                base_url = str(request.base_url).rstrip("/")
                referer = extra_headers.get("referer", SC_DOMAIN + "/")
                body = resp.text
                rewritten = _rewrite_m3u8(body, base_url, referer)
                return Response(
                    content=rewritten,
                    media_type="application/vnd.apple.mpegurl",
                    headers={"Access-Control-Allow-Origin": "*"},
                )

            return Response(
                content=resp.content,
                media_type=content_type,
                headers={"Access-Control-Allow-Origin": "*"},
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
