import logging
import os
import re
import random
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================
ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

SOURCE_URL = "https://vidsrc-embed.ru/embed"
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
IMPERSONATE = "chrome124"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]


# ============================================================================
# HELPERS
# ============================================================================
def clean_id(id_str: str) -> str:
    return id_str.split(":")[0] if ":" in id_str else id_str


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_sec_ch_ua(user_agent: str) -> str:
    if "Chrome" in user_agent and "Edg" in user_agent:
        return '"Chromium";v="128", "Not;A=Brand";v="24", "Microsoft Edge";v="128"'
    elif "Chrome" in user_agent and "Edg" not in user_agent:
        return '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"'
    return ""


def get_sec_ch_ua_platform(user_agent: str) -> str:
    if "Windows" in user_agent:
        return '"Windows"'
    elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
        return '"macOS"'
    elif "Linux" in user_agent:
        return '"Linux"'
    return '"Windows"'


def get_randomized_headers(base_dom: str) -> Dict[str, str]:
    user_agent = get_random_user_agent()
    sec_ch_ua = get_sec_ch_ua(user_agent)
    sec_ch_ua_platform = get_sec_ch_ua_platform(user_agent)

    headers: Dict[str, str] = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "priority": "u=1",
        "sec-ch-ua-mobile": "?0",
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "same-origin",
        "Referer": f"{base_dom}/",
        "Referrer-Policy": "origin",
        "User-Agent": user_agent,
    }

    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
        headers["sec-ch-ua-platform"] = sec_ch_ua_platform

    return headers


def parse_stremio_id(stremio_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    if stremio_id.startswith("tmdb:"):
        parts = stremio_id.split(":")
        if len(parts) >= 2:
            content_id = parts[1]
            season = parts[2] if len(parts) > 2 else None
            episode = parts[3] if len(parts) > 3 else None
            return content_id, season, episode

    parts = stremio_id.split(":")
    content_id = parts[0]
    season = parts[1] if len(parts) > 1 else None
    episode = parts[2] if len(parts) > 2 else None
    return content_id, season, episode


def build_embed_url(stremio_id: str, content_type: str) -> str:
    content_id, season, episode = parse_stremio_id(stremio_id)

    if content_type == "movie":
        return f"{SOURCE_URL}/movie/{content_id}"

    season = season or "1"
    episode = episode or "1"
    return f"{SOURCE_URL}/tv/{content_id}/{season}-{episode}"


async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={
                "external_source": "imdb_id",
                "api_key": TMDB_API_KEY,
                "language": "it-IT"
            },
            timeout=10,
            impersonate=IMPERSONATE
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
        logger.error(f"TMDB conversion error: {e}")
        return None


async def get_media_title(
    client: AsyncSession,
    stremio_id: str,
    content_type: str
) -> str:
    try:
        content_id, season, episode = parse_stremio_id(stremio_id)

        if content_id.startswith("tt"):
            tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
        else:
            tmdb_id = int(content_id)

        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}

        if content_type == "movie":
            r = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params=params,
                timeout=10,
                impersonate=IMPERSONATE
            )
            if r.status_code == 200:
                return r.json().get("title", "Film")
            return "Film"

        r = await client.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}",
            params=params,
            timeout=10,
            impersonate=IMPERSONATE
        )
        if r.status_code == 200:
            return r.json().get("name", f"Episodio {episode}")
        return f"Episodio {episode}"

    except Exception as e:
        logger.error(f"Title error: {e}")
        return "Unknown"


# ============================================================================
# HLS PARSER
# ============================================================================
def parse_hls_master(master_playlist_content: str, base_url: str) -> List[Dict[str, Any]]:
    qualities: List[Dict[str, Any]] = []
    lines = master_playlist_content.splitlines()

    for i, line in enumerate(lines):
        if "#EXT-X-STREAM-INF:" not in line:
            continue

        attrs_part = line.split(":", 1)[1] if ":" in line else ""
        bandwidth = 0
        resolution = None
        frame_rate = None
        codecs = None

        bw_match = re.search(r'BANDWIDTH=(\d+)', attrs_part)
        if bw_match:
            bandwidth = int(bw_match.group(1))

        res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', attrs_part)
        if res_match:
            resolution = f"{res_match.group(1)}x{res_match.group(2)}"
            height = int(res_match.group(2))
        else:
            height = 0

        fr_match = re.search(r'FRAME-RATE=([\d.]+)', attrs_part)
        if fr_match:
            try:
                frame_rate = float(fr_match.group(1))
            except Exception:
                frame_rate = None

        codecs_match = re.search(r'CODECS="([^"]+)"', attrs_part)
        if codecs_match:
            codecs = codecs_match.group(1)

        playlist_uri = None
        for j in range(i + 1, min(i + 4, len(lines))):
            if lines[j].strip() and not lines[j].startswith("#"):
                playlist_uri = lines[j].strip()
                break

        if not playlist_uri:
            continue

        playlist_url = playlist_uri if playlist_uri.startswith("http") else urljoin(base_url, playlist_uri)

        if height >= 1080:
            title = f"{resolution} (1080p)"
        elif height >= 720:
            title = f"{resolution} (720p)"
        elif height >= 480:
            title = f"{resolution} (480p)"
        elif height >= 360:
            title = f"{resolution} (360p)"
        elif resolution:
            title = resolution
        elif bandwidth > 5000000:
            title = "High Quality"
        elif bandwidth > 2000000:
            title = "Medium Quality"
        else:
            title = "Low Quality"

        qualities.append({
            "resolution": resolution,
            "bandwidth": bandwidth,
            "codecs": codecs,
            "frame_rate": frame_rate,
            "url": playlist_url,
            "title": title
        })

    qualities.sort(key=lambda x: x.get("bandwidth", 0), reverse=True)
    return qualities


async def fetch_and_parse_hls(url: str, client: AsyncSession) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(url, timeout=15, impersonate=IMPERSONATE)
        if r.status_code != 200:
            return None

        content = r.text
        if "#EXT-X-STREAM-INF" not in content:
            return None

        return {
            "masterUrl": url,
            "qualities": parse_hls_master(content, url)
        }
    except Exception as e:
        logger.warning(f"HLS parse failed: {e}")
        return None


# ============================================================================
# EXTRACTOR
# ============================================================================
class StremsrcStyleExtractor:
    def __init__(self):
        self.base_dom = "https://cloudnestra.com"

    async def servers_load(self, html: str) -> Tuple[List[Dict[str, Optional[str]]], str]:
        soup = BeautifulSoup(html, "lxml")
        servers: List[Dict[str, Optional[str]]] = []
        title = soup.title.text.strip() if soup.title else ""

        iframe = soup.find("iframe")
        base = iframe.get("src") if iframe else None
        if base:
            if base.startswith("//"):
                base = "https:" + base
            try:
                self.base_dom = f"{re.match(r'^https?://[^/]+', base).group(0)}"
            except Exception:
                pass

        for server in soup.select(".serversList .server"):
            servers.append({
                "name": server.get_text(strip=True),
                "dataHash": server.get("data-hash")
            })

        return servers, title

    async def rcp_grabber(self, html: str) -> Optional[str]:
        match = re.search(r"src:\s*'([^']*)'", html)
        return match.group(1) if match else None

    async def prorcp_handler(self, prorcp: str, client: AsyncSession) -> Optional[str]:
        try:
            r = await client.get(
                f"{self.base_dom}/prorcp/{prorcp}",
                headers=get_randomized_headers(self.base_dom),
                timeout=20,
                impersonate=IMPERSONATE
            )
            if r.status_code != 200:
                return None

            match = re.search(r"file:\s*'([^']*)'", r.text)
            return match.group(1) if match else None
        except Exception as e:
            logger.warning(f"prorcp handler failed: {e}")
            return None

    async def get_streams(self, stremio_id: str, content_type: str, client: AsyncSession) -> Dict[str, Any]:
        streams: List[Dict[str, Any]] = []

        try:
            embed_url = build_embed_url(stremio_id, content_type)
            media_title = await get_media_title(client, stremio_id, content_type)

            embed = await client.get(
                embed_url,
                headers=get_randomized_headers(self.base_dom),
                timeout=20,
                impersonate=IMPERSONATE
            )

            if embed.status_code != 200:
                logger.warning(f"Embed failed: {embed.status_code}")
                return {"streams": []}

            servers, _ = await self.servers_load(embed.text)
            if not servers:
                logger.warning("No servers found")
                return {"streams": []}

            rcp_results: List[str] = []
            for server in servers:
                data_hash = server.get("dataHash")
                if not data_hash:
                    continue

                try:
                    headers = get_randomized_headers(self.base_dom)
                    headers["Sec-Fetch-Dest"] = ""

                    r = await client.get(
                        f"{self.base_dom}/rcp/{data_hash}",
                        headers=headers,
                        timeout=20,
                        impersonate=IMPERSONATE
                    )
                    if r.status_code == 200:
                        rcp_results.append(r.text)
                except Exception as e:
                    logger.warning(f"RCP fetch failed: {e}")

            api_responses = []
            for html in rcp_results:
                rcp_data = await self.rcp_grabber(html)
                if not rcp_data:
                    continue

                if rcp_data.startswith("/prorcp/"):
                    stream_url = await self.prorcp_handler(rcp_data.replace("/prorcp/", ""), client)
                    if not stream_url:
                        continue

                    hls_data = await fetch_and_parse_hls(stream_url, client)

                    api_responses.append({
                        "name": media_title,
                        "stream": stream_url,
                        "referer": self.base_dom,
                        "hlsData": hls_data
                    })

            for st in api_responses:
                if not st.get("stream"):
                    continue

                behavior_hints = {
                    "proxyHeaders": {
                        "request": {
                            "Sec-Fetch-Dest": "iframe",
                            "Referer": f"{self.base_dom}/",
                            "User-Agent": get_random_user_agent()
                        }
                    },
                    "notWebReady": True,
                    "bingeGroup": "stremsrc"
                }

                hls_data = st.get("hlsData")
                if hls_data and hls_data.get("qualities"):
                    streams.append({
                        "name": "🛸 Auto",
                        "title": f"{st['name']} - VidSRC/Cloudnestra Auto",
                        "url": st["stream"],
                        "behaviorHints": behavior_hints
                    })

                    for quality in hls_data["qualities"]:
                        streams.append({
                            "name": f"🛸 {quality['title']}",
                            "title": f"{st['name']} - VidSRC/Cloudnestra {quality['title']}",
                            "url": quality["url"],
                            "behaviorHints": behavior_hints
                        })
                else:
                    streams.append({
                        "name": "🛸 Stream",
                        "title": f"{st['name']} - VidSRC/Cloudnestra",
                        "url": st["stream"],
                        "behaviorHints": behavior_hints
                    })

        except Exception as e:
            logger.error(f"Extractor error: {e}")

        return {"streams": streams}


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

extractor = StremsrcStyleExtractor()


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


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
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "VidSRC-style Stream via FastAPI",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt", "tmdb"],
        "behaviorHints": {"configurable": False}
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)

        async with AsyncSession(impersonate=IMPERSONATE, timeout=25) as client:
            data = await extractor.get_streams(id, type, client)

        return respond_with(data or {"streams": []})

    except Exception as e:
        logger.error(f"Endpoint stream error: {e}")
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
