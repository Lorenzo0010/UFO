import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fake_headers import Headers
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

load_dotenv()

ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
VIXSRC_DOMAIN = "https://vixsrc.to"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
IMPERSONATE = "chrome110"

PROXY_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": f"{VIXSRC_DOMAIN}/",
    "Origin": VIXSRC_DOMAIN,
}


def clean_id(id_str: str) -> str:
    return id_str.split(":")[0] if ":" in id_str else id_str


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "*"
    return resp


def proxy_url(base_request_url: str, target_url: str) -> str:
    """Wrappa un URL VixSrc nel proxy locale."""
    encoded = quote(target_url, safe="")
    return f"{base_request_url}U0MQ/proxy/hls?url={encoded}"


def resolve_url(url: str, base: str) -> str:
    """Risolve URL relativi rispetto al base URL del manifest."""
    if url.startswith("http"):
        return url
    return urljoin(base, url)


def rewrite_m3u8(content: str, manifest_url: str, proxy_base: str) -> str:
    """
    Riscrive un manifest M3U8 sostituendo tutti gli URL con versioni proxate:
    - righe segmento (.ts, .m4s, .mp4, .aac, ecc.)
    - URI= nei tag #EXT-X-KEY, #EXT-X-MAP, #EXT-X-MEDIA, ecc.
    - eventuali sub-playlist .m3u8
    """
    lines = content.splitlines()
    out = []

    for line in lines:
        stripped = line.strip()

        # Righe di tag con URI=
        if stripped.startswith("#") and 'URI="' in stripped:
            def replace_uri(m):
                inner = m.group(1)
                resolved = resolve_url(inner, manifest_url)
                return f'URI="{proxy_url(proxy_base, resolved)}"'
            line = re.sub(r'URI="([^"]+)"', replace_uri, stripped)

        # Righe URI (segmenti o sub-playlist), non commenti
        elif stripped and not stripped.startswith("#"):
            resolved = resolve_url(stripped, manifest_url)
            line = proxy_url(proxy_base, resolved)

        out.append(line)

    return "\n".join(out)


async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=15,
            impersonate=IMPERSONATE,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("movie_results"):
            return data["movie_results"][0].get("id")
        if data.get("tv_results"):
            return data["tv_results"][0].get("id")
        return None
    except Exception as e:
        logger.error(f"❌ TMDB error: {e}")
        return None


class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = VIXSRC_DOMAIN
        self.random_headers = Headers()

    def build_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        h = self.random_headers.generate()
        h["User-Agent"] = USER_AGENT
        h["Referer"] = referer or f"{self.domain}/"
        h["Origin"] = self.domain
        return h

    def parse_stream_data(self, html: str) -> Optional[Dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            content = script.string or script.text or ""
            if "masterPlaylist" not in content:
                continue
            token_match = re.search(r"['\"]token['\"]\s*:\s*['\"]([^'\"]+)['\"]", content)
            expires_match = re.search(r"['\"]expires['\"]\s*:\s*['\"](\d+)['\"]", content)
            url_match = re.search(
                r"masterPlaylist\s*=\s*\{[^}]*url\s*:\s*['\"]([^'\"]+)['\"]",
                content, re.DOTALL
            )
            if not url_match:
                url_match = re.search(r"url\s*:\s*['\"]([^'\"]+)['\"]", content)
            if token_match and expires_match and url_match:
                return {
                    "token": token_match.group(1),
                    "expires": expires_match.group(1),
                    "url": url_match.group(1),
                    "fhd": "1" if "canPlayFHD" in content else "0",
                }
        return None

    def build_playlist_url(self, data: Dict[str, str]) -> str:
        playlist_url = data["url"]
        sep = "&" if "?" in playlist_url else "?"
        url = f"{playlist_url}{sep}token={data['token']}&expires={data['expires']}"
        if data.get("fhd") == "1" and "h=1" not in url:
            url += "&h=1"
        return url

    async def extract_playlist_url(self, page_url: str, client: AsyncSession) -> Optional[str]:
        try:
            response = await client.get(
                page_url, headers=self.build_headers(),
                timeout=20, impersonate=IMPERSONATE,
            )
            if response.status_code != 200:
                logger.warning(f"Page {page_url} -> {response.status_code}")
                return None
            parsed = self.parse_stream_data(response.text)
            if not parsed:
                logger.warning(f"masterPlaylist not found: {page_url}")
                return None
            url = self.build_playlist_url(parsed)
            logger.info(f"🎯 Playlist URL: {url}")
            return url
        except Exception as e:
            logger.error(f"❌ extract_playlist_url: {e}")
            return None

    async def get_streams(
        self, id: str, content_type: str, client: AsyncSession, base_url: str
    ) -> Dict[str, list]:
        streams = {"streams": []}
        try:
            content_id = clean_id(id)
            is_series = ":" in id
            season = episode = None

            if is_series:
                parts = id.split(":")
                if len(parts) >= 3:
                    content_id, season, episode = parts[0], parts[1], parts[2]

            if content_id.startswith("tt"):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                except ValueError:
                    return streams

            if content_type == "series" and is_series and season and episode:
                page_url = f"{self.domain}/tv/{tmdb_id}/{season}/{episode}/"
                filename = f"{content_id}-S{int(season):02d}E{int(episode):02d}.m3u8"
            elif content_type == "movie":
                page_url = f"{self.domain}/movie/{tmdb_id}/"
                filename = f"{content_id}.m3u8"
            else:
                return streams

            playlist_url = await self.extract_playlist_url(page_url, client)
            if not playlist_url:
                return streams

            proxied_url = proxy_url(base_url + "/", playlist_url)
            logger.info(f"✅ Proxied URL: {proxied_url}")

            streams["streams"].append({
                "name": "🛸 UFO",
                "description": self.domain,
                "title": "VixSrc via UFO proxy",
                "url": proxied_url,
                "filename": filename,
                "behaviorHints": {
                    "notWebReady": True,
                    "bingeGroup": "streamingcommunity",
                }
            })
        except Exception as e:
            logger.error(f"❌ get_streams: {e}")
        return streams


# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME} Addon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

extractor = StreamingCommunityExtractor()


@app.get("/")
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return respond_with({"status": "online", "addon": ADDON_NAME,
                         "manifest": f"{base_url}/U0MQ/manifest.json"})


@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "2.1.0",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Vercel",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    if type not in ["movie", "series"]:
        raise HTTPException(status_code=404)
    try:
        base_url = str(request.base_url).rstrip("/")
        async with AsyncSession() as client:
            data = await extractor.get_streams(id, type, client, base_url)
        return respond_with(data or {"streams": []})
    except Exception as e:
        logger.error(f"❌ /stream: {e}")
        return respond_with({"streams": []})


# ============================================================================
# HLS PROXY
# ============================================================================
@app.get("/U0MQ/proxy/hls")
async def hls_proxy(request: Request, url: str):
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    proxy_base = str(request.base_url)  # es. https://ufo-pearl-theta.vercel.app/

    try:
        async with AsyncSession() as client:
            response = await client.get(
                url,
                headers=PROXY_HEADERS,
                timeout=25,
                allow_redirects=True,
                impersonate=IMPERSONATE,
            )

        actual_url = str(response.url)  # URL finale dopo redirect
        content_type = response.headers.get("content-type", "application/octet-stream").lower()
        body_bytes = response.content

        logger.info(
            f"🛠 proxy: status={response.status_code} CT={content_type} "
            f"len={len(body_bytes)} url={actual_url[:80]}"
        )

        # Determina se è un manifest M3U8
        is_m3u8 = (
            b"#EXTM3U" in body_bytes[:32]
            or "mpegurl" in content_type
            or actual_url.endswith(".m3u8")
        )

        if is_m3u8:
            text = body_bytes.decode("utf-8", errors="replace")
            logger.info(f"📜 Raw M3U8 first 300 chars:\n{text[:300]}")
            rewritten = rewrite_m3u8(text, actual_url, proxy_base)
            return Response(
                content=rewritten.encode("utf-8"),
                status_code=200,
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
            )

        # Segmenti binari (.ts, .mp4, chiavi, ecc.) - pass-through
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            media_type=content_type,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"},
        )

    except Exception as e:
        logger.error(f"❌ HLS proxy error [{url[:60]}]: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ============================================================================
# DEBUG
# ============================================================================
@app.get("/U0MQ/debug/stream/{tmdb_id}")
async def debug_stream(request: Request, tmdb_id: str):
    try:
        base_url = str(request.base_url).rstrip("/")
        async with AsyncSession() as client:
            data = await extractor.get_streams(tmdb_id, "movie", client, base_url)
        return respond_with({
            "tmdb_id": tmdb_id,
            "streams": data.get("streams", []),
            "valid": len(data.get("streams", [])) > 0
        })
    except Exception as e:
        return respond_with({"error": str(e)})


@app.get("/U0MQ/debug/proxy-raw/{tmdb_id}")
async def debug_proxy_raw(request: Request, tmdb_id: str):
    """
    Chiama direttamente il playlist URL e mostra il body grezzo.
    Utile per capire cosa restituisce VixSrc prima della riscrittura.
    """
    try:
        async with AsyncSession() as client:
            ext = StreamingCommunityExtractor()
            playlist_url = await ext.extract_playlist_url(
                f"{VIXSRC_DOMAIN}/movie/{tmdb_id}/", client
            )
            if not playlist_url:
                return respond_with({"error": "playlist_url not found"})

            resp = await client.get(
                playlist_url, headers=PROXY_HEADERS,
                timeout=20, allow_redirects=True, impersonate=IMPERSONATE,
            )
            body = resp.text
            return respond_with({
                "playlist_url": playlist_url,
                "final_url": str(resp.url),
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type", ""),
                "body_length": len(body),
                "body_preview": body[:1000],
                "is_m3u8": body.strip().startswith("#EXTM3U"),
            })
    except Exception as e:
        return respond_with({"error": str(e)})
