import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fake_headers import Headers
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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
        headers = self.random_headers.generate()
        headers["User-Agent"] = USER_AGENT
        headers["Referer"] = referer or f"{self.domain}/"
        headers["Origin"] = self.domain
        return headers

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
                content,
                re.DOTALL,
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
        """Estrae l'URL del playlist (non ancora proxato) dalla pagina VixSrc."""
        try:
            response = await client.get(
                page_url,
                headers=self.build_headers(),
                timeout=20,
                impersonate=IMPERSONATE,
            )
            if response.status_code != 200:
                logger.warning(f"Page fetch failed {response.status_code}: {page_url}")
                return None

            parsed = self.parse_stream_data(response.text)
            if not parsed:
                logger.warning(f"masterPlaylist not found in: {page_url}")
                return None

            playlist_url = self.build_playlist_url(parsed)
            logger.info(f"🎯 Playlist URL: {playlist_url}")
            return playlist_url

        except Exception as e:
            logger.error(f"❌ extract_playlist_url error: {e}")
            return None

    async def get_streams(self, id: str, content_type: str, client: AsyncSession, base_url: str) -> Dict[str, list]:
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

            # Invece di dare a Stremio l'URL diretto di VixSrc (che richiederebbe
            # header specifici), passiamo attraverso il nostro proxy /U0MQ/proxy/hls
            encoded = quote(playlist_url, safe="")
            proxied_url = f"{base_url}/U0MQ/proxy/hls?url={encoded}"

            logger.info(f"✅ Proxied stream URL: {proxied_url}")

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
            logger.error(f"❌ get_streams error: {e}")
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
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/U0MQ/manifest.json"
    })


@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "2.0.0",
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
        logger.error(f"❌ /stream route error: {e}")
        return respond_with({"streams": []})


# ============================================================================
# HLS PROXY — recupera /playlist/XXXXX con i giusti header e lo serve a Stremio
# ============================================================================
@app.get("/U0MQ/proxy/hls")
async def hls_proxy(request: Request, url: str):
    """
    Proxy trasparente per HLS manifest/segment.
    Stremio chiama questo endpoint; noi chiamiamo VixSrc con i giusti header.
    Se il body è un manifest M3U8, riscriviamo i segmenti relativi in URL assoluti.
    """
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        async with AsyncSession() as client:
            response = await client.get(
                url,
                headers=PROXY_HEADERS,
                timeout=20,
                allow_redirects=True,
                impersonate=IMPERSONATE,
            )

        content_type = response.headers.get("content-type", "application/octet-stream")
        body = response.content

        # Se è un manifest M3U8, riscriviamo i segmenti relativi
        if b"#EXTM3U" in body[:20]:
            content_type = "application/vnd.apple.mpegurl"
            text = body.decode("utf-8", errors="replace")

            # Base URL per i segmenti relativi: tutto fino all'ultimo /
            base = url.rsplit("/", 1)[0] + "/"

            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    if stripped.startswith("http"):
                        # segmento assoluto → proxalo
                        enc = quote(stripped, safe="")
                        line = f"{request.base_url}U0MQ/proxy/hls?url={enc}"
                    elif stripped.startswith("/"):
                        # path assoluto
                        enc = quote(f"{VIXSRC_DOMAIN}{stripped}", safe="")
                        line = f"{request.base_url}U0MQ/proxy/hls?url={enc}"
                    else:
                        # path relativo
                        enc = quote(f"{base}{stripped}", safe="")
                        line = f"{request.base_url}U0MQ/proxy/hls?url={enc}"
                lines.append(line)

            body = "\n".join(lines).encode("utf-8")
            logger.info(f"📺 M3U8 proxied and rewritten ({len(lines)} lines)")

        return Response(
            content=body,
            status_code=response.status_code,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
            },
        )

    except Exception as e:
        logger.error(f"❌ HLS proxy error for {url}: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ============================================================================
# DEBUG
# ============================================================================
@app.get("/U0MQ/debug/movie/{tmdb_id}")
async def debug_movie(tmdb_id: str):
    try:
        async with AsyncSession() as client:
            url = f"{VIXSRC_DOMAIN}/movie/{tmdb_id}/"
            response = await client.get(url, headers=PROXY_HEADERS, timeout=20, impersonate=IMPERSONATE)
            soup = BeautifulSoup(response.text, "lxml")
            scripts = []
            for i, s in enumerate(soup.find_all("script")):
                content = s.string or s.text or ""
                if len(content) > 20:
                    scripts.append({
                        "index": i,
                        "has_masterPlaylist": "masterPlaylist" in content,
                        "has_token": "token" in content,
                        "preview": content[:500]
                    })
            return respond_with({
                "status_code": response.status_code,
                "final_url": str(response.url),
                "scripts": scripts[:10],
            })
    except Exception as e:
        return respond_with({"error": str(e)})


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
