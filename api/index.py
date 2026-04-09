import logging
import re
import os
from typing import Dict, Optional, Any
from urllib.parse import quote, urljoin, urlparse, urlencode, parse_qs
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================
ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

VIXSRC_DOMAIN = "https://vixsrc.to"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
ADDON_BASE_URL = os.getenv("ADDON_BASE_URL", "").rstrip("/")

PROXY_HEADERS = {
    "User-Agent": User_Agent,
    "Referer": f"{VIXSRC_DOMAIN}/",
    "Origin": VIXSRC_DOMAIN,
}


def clean_id(id_str: str) -> str:
    return id_str.split(":")[0] if ":" in id_str else id_str


def make_proxy_url(base_url: str, target_url: str) -> str:
    """Costruisce l'URL del proxy interno per un segmento/manifest."""
    encoded = quote(target_url, safe="")
    return f"{base_url}/U0MQ/proxy/hls?url={encoded}"


def get_m3u8_base(url: str) -> str:
    """
    Calcola la base URL corretta per risolvere URI relativi in un M3U8.
    Rimuove la query string e prende la directory del path.
    Es: https://cdn.vixsrc.to/hls/abc/index.m3u8?token=x
     -> https://cdn.vixsrc.to/hls/abc/
    """
    parsed = urlparse(url)
    # Rimuovi query string, prendi solo schema+netloc+path
    clean_path = parsed.scheme + "://" + parsed.netloc + parsed.path
    # Prendi la directory (tutto prima dell'ultimo /)
    base = clean_path.rsplit("/", 1)[0] + "/"
    return base


async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("movie_results"):
                return data["movie_results"][0].get("id")
            if data.get("tv_results"):
                return data["tv_results"][0].get("id")
        return None
    except Exception as e:
        logger.error(f"❌ TMDB error: {e}")
        return None


# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = VIXSRC_DOMAIN
        self.random_headers = Headers()

    async def extract_playlist_url(self, link: str, client: AsyncSession) -> Optional[str]:
        """Estrae il playlist URL grezzo da VixSrc."""
        try:
            logger.info(f"🔍 Fetching: {link}")
            headers = self.random_headers.generate()
            headers["Referer"] = f"{self.domain}/"
            headers["User-Agent"] = User_Agent

            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200:
                logger.warning(f"⚠️ Status {response.status_code} for {link}")
                return None

            soup = BeautifulSoup(response.text, "lxml")
            for script in soup.find_all("script"):
                if not script.string:
                    continue
                if "token" not in script.string or "expires" not in script.string:
                    continue

                video_data = script.string
                token_match = re.search(r"'token':\s*'(\w+)'", video_data)
                expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
                url_match = re.search(r"url:\s*'([^']+)'", video_data)

                if not all([token_match, expires_match, url_match]):
                    continue

                token = token_match.group(1)
                expires = expires_match.group(1)
                server_url = url_match.group(1)

                sep = "&" if "?" in server_url else "?"
                playlist_url = f"{server_url}{sep}token={token}&expires={expires}"

                if "window.canPlayFHD = true" in video_data:
                    playlist_url += "&h=1"

                logger.info(f"🎯 Playlist URL: {playlist_url}")
                return playlist_url

            logger.warning(f"⚠️ Token non trovato in {link}")
            return None

        except Exception as e:
            logger.error(f"❌ Extractor error: {e}")
            return None

    async def get_streams(self, id: str, base_url: str, client: AsyncSession) -> Dict:
        streams = {"streams": []}
        try:
            is_series = False
            season = episode = None
            content_id = clean_id(id)

            if ":" in id:
                parts = id.split(":")
                content_id = parts[0]
                if len(parts) >= 3:
                    season, episode = parts[1], parts[2]
                    is_series = True

            if content_id.startswith("tt"):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                except ValueError:
                    return streams

            page_url = (
                f"{self.domain}/tv/{tmdb_id}/{season}/{episode}/"
                if is_series
                else f"{self.domain}/movie/{tmdb_id}/"
            )

            playlist_url = await self.extract_playlist_url(page_url, client)

            if playlist_url:
                proxied_url = make_proxy_url(base_url, playlist_url)
                logger.info(f"✅ Proxied stream: {proxied_url}")

                streams["streams"].append({
                    "name": "🛸UFO",
                    "description": "VixSrc",
                    "url": proxied_url,
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": "streamingcommunity"
                    }
                })
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")
        return streams


# ============================================================================
# FASTAPI
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME} Addon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
extractor = StreamingCommunityExtractor()


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def get_base_url(request: Request) -> str:
    """Restituisce la base URL dell'addon (usa ADDON_BASE_URL se impostata)."""
    if ADDON_BASE_URL:
        return ADDON_BASE_URL
    return str(request.base_url).rstrip("/")


# ============================================================================
# HLS PROXY INTERNO
# ============================================================================
@app.head("/U0MQ/proxy/hls")
async def hls_proxy_head(url: str):
    return StreamingResponse(
        iter([]),
        media_type="video/mp2t",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@app.get("/U0MQ/proxy/hls")
async def hls_proxy(request: Request, url: str):
    """
    Proxy trasparente per HLS:
    - Scarica manifest/segmenti con gli header VixSrc richiesti
    - Riscrive gli URL nel manifest per passare anch'essi per questo proxy
    - Gestisce sia master playlist (multi-qualità) che media playlist (segmenti .ts)
    """
    base_url = get_base_url(request)
    try:
        client = AsyncSession()
        resp = await client.get(
            url,
            headers=PROXY_HEADERS,
            timeout=20,
            allow_redirects=True
        )

        content_type = resp.headers.get("content-type", "")
        logger.info(f"🛠 proxy: status={resp.status_code} CT={content_type[:40]} url={url[:80]}")

        if resp.status_code != 200:
            await client.close()
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        # Determina se è un M3U8 (master o media playlist)
        is_m3u8 = (
            "mpegurl" in content_type.lower()
            or "x-mpegurl" in content_type.lower()
            or url.split("?")[0].lower().endswith(".m3u8")
        )

        if is_m3u8:
            text = resp.text
            await client.close()

            # Calcola la base corretta rimuovendo query string e prendendo la directory
            media_base = get_m3u8_base(url)

            # Controlla se è un master playlist (contiene varianti .m3u8)
            is_master = "#EXT-X-STREAM-INF" in text or "#EXT-X-MEDIA" in text
            playlist_type = "master" if is_master else "media"
            logger.info(f"📺 M3U8 {playlist_type} proxied and rewritten (base: {media_base})")

            rewritten = rewrite_m3u8(text, media_base, base_url)
            return StreamingResponse(
                iter([rewritten.encode()]),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        # Segmento .ts o altro binario: streaming chunk per chunk
        async def stream_and_close():
            try:
                async for chunk in resp.aiter_content():
                    yield chunk
            finally:
                await client.close()

        return StreamingResponse(
            stream_and_close(),
            media_type=content_type or "video/mp2t",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Proxy error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


def rewrite_m3u8(content: str, base_url_media: str, addon_base: str) -> str:
    """
    Riscrive ogni URI nel manifest M3U8 per passare attraverso il proxy interno.
    Gestisce URI relativi e assoluti, sia in righe di dati che negli attributi URI="...".
    """
    lines = content.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            # Riscrivi URI="..." negli attributi (es. #EXT-X-KEY, #EXT-X-MEDIA)
            line = re.sub(
                r'URI="([^"]+)"',
                lambda m: f'URI="{make_proxy_url(addon_base, resolve_url(m.group(1), base_url_media))}"',
                line
            )
            result.append(line)
        elif stripped:
            # Riga dati: può essere un .m3u8 (variante) o un .ts (segmento)
            resolved = resolve_url(stripped, base_url_media)
            result.append(make_proxy_url(addon_base, resolved))
        else:
            result.append(line)
    return "\n".join(result)


def resolve_url(uri: str, base: str) -> str:
    """Risolve un URI relativo rispetto alla base URL del manifest."""
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    return urljoin(base, uri)


# ============================================================================
# ROUTES
# ============================================================================
@app.get("/")
async def root(request: Request):
    base_url = get_base_url(request)
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/U0MQ/manifest.json"
    })


@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "VixSrc Stream",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        base_url = get_base_url(request)
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, base_url, client)
        return respond_with(streams_data or {"streams": []})
    except Exception:
        return respond_with({"streams": []})


@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({
        "meta": {
            "id": id, "type": type,
            "name": ADDON_NAME, "poster": ADDON_LOGO
        }
    })


@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
