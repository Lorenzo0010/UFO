import json
import logging
import re
import os
import urllib.parse
from typing import Dict, Optional, Any

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

CONFIG = {
    "Siti": {
        "StreamingCommunity": {
            "url": "https://vixsrc.to",
            "enabled": "1"
        }
    }
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')


def clean_id(id_str: str) -> str:
    return id_str.split(':')[0] if ':' in id_str else id_str


async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=10
        )
        logger.info(f"🎬 TMDB lookup {imdb_id} → status {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            logger.info(f"🎬 TMDB data: movie={len(data.get('movie_results',[]))}, tv={len(data.get('tv_results',[]))}")
            if data.get('movie_results'):
                return data['movie_results'][0].get('id')
            if data.get('tv_results'):
                return data['tv_results'][0].get('id')
        return None
    except Exception as e:
        logger.error(f"❌ TMDB Error: {e}")
        return None


# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> Optional[str]:
        try:
            logger.info(f"🔍 Fetching page: {link}")
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent

            response = await client.get(link, headers=headers, timeout=15)
            logger.info(f"📄 Page status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"❌ Bad status {response.status_code} for {link}")
                return None

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            logger.info(f"📜 Found {len(scripts)} script tags")

            for i, script in enumerate(scripts):
                if not script.string:
                    continue
                content = script.string
                if "token" in content and "expires" in content:
                    logger.info(f"🔑 Found token/expires in script #{i}")

                    token_match = re.search(r"'token':\s*'(\w+)'", content)
                    expires_match = re.search(r"'expires':\s*'(\d+)'", content)
                    url_match = re.search(r"url:\s*'([^']+)'", content)

                    logger.info(f"  token={bool(token_match)}, expires={bool(expires_match)}, url={bool(url_match)}")

                    if not all([token_match, expires_match, url_match]):
                        token_match = re.search(r'"token":\s*"(\w+)"', content)
                        expires_match = re.search(r'"expires":\s*"(\d+)"', content)
                        url_match = re.search(r'"url":\s*"([^"]+)"', content)
                        logger.info(f"  Alt pattern: token={bool(token_match)}, expires={bool(expires_match)}, url={bool(url_match)}")

                    if all([token_match, expires_match, url_match]):
                        token = token_match.group(1)
                        expires = expires_match.group(1)
                        server_url = url_match.group(1)
                        logger.info(f"  server_url={server_url}")

                        sep = "&" if "?" in server_url else "?"
                        final_url = f"{server_url}{sep}token={token}&expires={expires}"

                        if "window.canPlayFHD = true" in content:
                            final_url += "&h=1"

                        logger.info(f"✅ Constructed URL: {final_url}")
                        return final_url

            logger.error(f"❌ No stream data found in scripts for {link}")
            return None

        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}", exc_info=True)
            return None

    async def get_streams(self, id: str, request: Request, client: AsyncSession) -> Dict:
        streams = {'streams': []}
        try:
            logger.info(f"🚀 get_streams called with id={id}")
            is_series = False
            season = None
            episode = None
            content_id = clean_id(id)

            if ':' in id:
                parts = id.split(':')
                content_id = parts[0]
                if len(parts) >= 3:
                    season, episode = parts[1], parts[2]
                    is_series = True

            logger.info(f"  content_id={content_id}, is_series={is_series}, season={season}, ep={episode}")

            tmdb_id = None
            if content_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                logger.info(f"  tmdb_id resolved: {tmdb_id}")
                if not tmdb_id:
                    logger.error(f"❌ Could not resolve TMDB ID for {content_id}")
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                except ValueError:
                    logger.error(f"❌ Invalid content_id: {content_id}")
                    return streams

            url = (
                f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/'
                if is_series
                else f'{self.domain}/movie/{tmdb_id}/'
            )
            logger.info(f"🌐 VixSrc URL: {url}")

            stream_url = await self.extract_vixcloud_url(url, client)

            if stream_url:
                base_url = str(request.base_url).rstrip("/")
                encoded = urllib.parse.quote(stream_url, safe='')
                proxy_url = f"{base_url}/U0MQ/proxy/hls?url={encoded}"
                logger.info(f"🎯 Final proxy URL: {proxy_url}")

                streams['streams'].append({
                    "name": "🛸UFO",
                    "description": f"StreamingCommunity • {self.domain}",
                    "url": proxy_url,
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": "ufo-sc"
                    }
                })
            else:
                logger.error(f"❌ No stream_url found for {url}")

        except Exception as e:
            logger.error(f"❌ get_streams Error: {e}", exc_info=True)

        logger.info(f"📦 Returning {len(streams['streams'])} streams")
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


def respond_with(data: Any, cache: bool = False) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    if cache:
        resp.headers["Cache-Control"] = "max-age=3600, public"
    else:
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def make_proxy_uri(uri: str, decoded_url: str, proxy_base: str) -> str:
    """Converte un URI (assoluto o relativo) in un URL proxiato."""
    if uri.startswith("http"):
        return f'{proxy_base}{urllib.parse.quote(uri, safe="")}'
    else:
        base = decoded_url.rsplit("/", 1)[0]
        absolute = f"{base}/{uri.lstrip('/')}"
        return f'{proxy_base}{urllib.parse.quote(absolute, safe="")}'


# ============================================================================
# PROXY HLS
# ============================================================================
@app.get("/U0MQ/proxy/hls")
async def proxy_hls(url: str, request: Request):
    """
    Proxy trasparente per manifest HLS di VixSrc/VixCloud.
    Riscrive tutti gli URI interni (audio, video, subtitle, segmenti .ts,
    e la chiave AES-128) facendoli passare attraverso questo proxy.
    """
    try:
        decoded_url = urllib.parse.unquote(url)
        base_url = str(request.base_url).rstrip("/")
        proxy_base = f"{base_url}/U0MQ/proxy/hls?url="

        headers = {
            "User-Agent": User_Agent,
            "Referer": "https://vixsrc.to/",
            "Origin": "https://vixsrc.to",
        }

        async with AsyncSession() as client:
            resp = await client.get(decoded_url, headers=headers, timeout=15)

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Upstream error")

        content_type = resp.headers.get("content-type", "")
        body = resp.text

        # Se è un manifest HLS, riscriviamo tutti gli URI
        if "mpegurl" in content_type or body.strip().startswith("#EXTM3U"):
            lines = body.splitlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()

                # --- Riga #EXT-X-KEY: riscriviamo solo l'URI della chiave AES ---
                if stripped.startswith("#EXT-X-KEY:"):
                    def rewrite_key(m):
                        key_uri = m.group(1)
                        proxied = make_proxy_uri(key_uri, decoded_url, proxy_base)
                        return f'URI="{proxied}"'
                    line = re.sub(r'URI="([^"]+)"', rewrite_key, line)
                    new_lines.append(line)

                # --- Righe con URI= dentro direttive (#EXT-X-MEDIA, #EXT-X-STREAM-INF, ecc.) ---
                elif stripped.startswith(("#EXT-X-MEDIA:", "#EXT-X-STREAM-INF:", "#EXT-X-I-FRAME-STREAM-INF:")):
                    def rewrite_uri(m):
                        uri_val = m.group(1)
                        proxied = make_proxy_uri(uri_val, decoded_url, proxy_base)
                        return f'URI="{proxied}"'
                    line = re.sub(r'URI="([^"]+)"', rewrite_uri, line)
                    new_lines.append(line)

                # --- URI di segmento (riga non-commento non vuota) ---
                elif stripped and not stripped.startswith("#"):
                    proxied = make_proxy_uri(stripped, decoded_url, proxy_base)
                    new_lines.append(proxied)

                # --- Tutto il resto (commenti, tag senza URI) ---
                else:
                    new_lines.append(line)

            rewritten = "\n".join(new_lines)
            return Response(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache",
                }
            )

        # Segmenti .ts, chiavi .key, subtitle .vtt — passa direttamente
        return Response(
            content=resp.content,
            media_type=content_type or "application/octet-stream",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Proxy error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# ROUTES
# ============================================================================
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
    config = {
        "id": "org.stremio.ufo.streamingcommunity",
        "version": "1.4.1",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Vercel Proxy",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    }
    return respond_with(config, cache=True)


@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    logger.info(f"📡 Stream request: type={type}, id={id}")
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, request, client)
        if not streams_data:
            streams_data = {"streams": []}
        logger.info(f"📡 Returning streams: {json.dumps(streams_data)}")
        return respond_with(streams_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Stream endpoint error: {e}", exc_info=True)
        return respond_with({"streams": []})


@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({
        "meta": {
            "id": id,
            "type": type,
            "name": ADDON_NAME,
            "poster": ADDON_LOGO
        }
    })


@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
