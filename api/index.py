import logging
import os
import re
from typing import Dict, Optional, Any, List
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fake_headers import Headers
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

CONFIG = {
    "Siti": {
        "StreamingCommunity": {
            "url": "https://vixsrc.to",
            "enabled": "1"
        }
    }
}

TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
IMPERSONATE = "chrome124"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# HELPERS
# ============================================================================
def clean_id(id_str: str) -> str:
    return id_str.split(":")[0] if ":" in id_str else id_str


def build_headers(referer: Optional[str] = None, origin: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    return headers


def ensure_m3u8_and_params(url: str, token: Optional[str], expires: Optional[str], add_h: bool = False) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if token:
        query["token"] = token
    if expires:
        query["expires"] = expires
    if add_h and "h" not in query:
        query["h"] = "1"

    path = parsed.path
    if not path.endswith(".m3u8"):
        path = f"{path}.m3u8"

    new_parsed = parsed._replace(path=path, query=urlencode(query))
    return urlunparse(new_parsed)


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
            logger.warning(f"TMDB find failed: {response.status_code}")
            return None

        data = response.json()
        if data.get("movie_results"):
            return data["movie_results"][0].get("id")
        if data.get("tv_results"):
            return data["tv_results"][0].get("id")
        return None

    except Exception as e:
        logger.error(f"Errore conversione IMDb -> TMDB: {e}")
        return None


async def get_media_title(
    client: AsyncSession,
    tmdb_id: int,
    is_series: bool,
    season: Optional[str] = None,
    episode: Optional[str] = None
) -> str:
    try:
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}

        if not is_series:
            response = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params=params,
                timeout=10,
                impersonate=IMPERSONATE
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"

        response = await client.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}",
            params=params,
            timeout=10,
            impersonate=IMPERSONATE
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("name", f"Episodio {episode}")
        return f"Episodio {episode}"

    except Exception as e:
        logger.error(f"Errore recupero titolo TMDB: {e}")
        return f"Episodio {episode}" if is_series else f"Film {tmdb_id}"


# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG["Siti"]["StreamingCommunity"]["url"].rstrip("/")
        self.random_headers = Headers()

    def _extract_player_data(self, html: str) -> Optional[Dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        scripts = soup.find_all("script")

        joined_scripts = "\n".join(
            script.get_text("\n", strip=False) for script in scripts if script.get_text(strip=False)
        )

        # Pattern classico
        token_match = re.search(r"'token'\s*:\s*'([^']+)'", joined_scripts)
        expires_match = re.search(r"'expires'\s*:\s*'([^']+)'", joined_scripts)
        url_match = re.search(r"url\s*:\s*'([^']+)'", joined_scripts)

        if token_match and expires_match and url_match:
            return {
                "token": token_match.group(1),
                "expires": expires_match.group(1),
                "url": url_match.group(1),
                "raw": joined_scripts
            }

        # Fallback: cerca qualunque m3u8 diretta
        m3u8_match = re.search(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', joined_scripts)
        if m3u8_match:
            return {
                "token": "",
                "expires": "",
                "url": m3u8_match.group(1),
                "raw": joined_scripts
            }

        return None

    async def _detect_quality(self, m3u8_url: str, headers: Dict[str, str], client: AsyncSession) -> Dict[str, Any]:
        detected_quality = "Auto"
        max_height = 0

        try:
            res = await client.get(
                m3u8_url,
                headers=headers,
                timeout=10,
                impersonate=IMPERSONATE
            )

            logger.info(f"M3U8 metadata status: {res.status_code}")

            if res.status_code == 200:
                lines = res.text.splitlines()
                for line in lines:
                    if "RESOLUTION=" in line:
                        match = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
                        if match:
                            height = int(match.group(2))
                            max_height = max(max_height, height)

                if max_height > 0:
                    detected_quality = f"{max_height}p"
                else:
                    detected_quality = "HLS"

        except Exception as e:
            logger.warning(f"Impossibile analizzare metadata m3u8: {e}")

        return {
            "quality": detected_quality,
            "height": max_height
        }

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict[str, Any]]:
        try:
            page_headers = build_headers(
                referer=f"{self.domain}/",
                origin=self.domain
            )

            logger.info(f"Fetching page: {link}")

            response = await client.get(
                link,
                headers=page_headers,
                timeout=20,
                impersonate=IMPERSONATE
            )

            logger.info(f"Page status: {response.status_code}")

            if response.status_code != 200:
                return []

            player_data = self._extract_player_data(response.text)
            if not player_data:
                logger.warning("Nessun dato player trovato nella pagina")
                return []

            raw_script = player_data.get("raw", "")
            server_url = player_data["url"]
            token = player_data.get("token")
            expires = player_data.get("expires")

            final_url = server_url
            if ".m3u8" not in server_url or token or expires:
                final_url = ensure_m3u8_and_params(
                    server_url,
                    token=token,
                    expires=expires,
                    add_h=("window.canPlayFHD = true" in raw_script)
                )

            hls_headers = build_headers(
                referer=f"{self.domain}/",
                origin=self.domain
            )

            quality_info = await self._detect_quality(final_url, hls_headers, client)

            if quality_info["height"] == 0 and "window.canPlayFHD = true" in raw_script:
                quality_info["quality"] = "1080p"
            elif quality_info["height"] == 0 and quality_info["quality"] == "Auto":
                quality_info["quality"] = "720p"

            logger.info(f"Final URL: {final_url}")
            logger.info(f"Detected quality: {quality_info['quality']}")

            return [{
                "quality": quality_info["quality"],
                "url": final_url,
                "height": quality_info["height"]
            }]

        except Exception as e:
            logger.error(f"Extractor error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict[str, List[Dict[str, Any]]]:
        streams = {"streams": []}

        try:
            is_series = False
            season = None
            episode = None
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

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            source_url = (
                f"{self.domain}/tv/{tmdb_id}/{season}/{episode}/"
                if is_series else
                f"{self.domain}/movie/{tmdb_id}/"
            )

            results = await self.extract_vixcloud_url(source_url, client)

            for res in results:
                streams["streams"].append({
                    "name": f"🛸 {res['quality']}",
                    "title": media_title,
                    "url": res["url"],
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": "streamingcommunity",
                        "proxyHeaders": {
                            "request": {
                                "User-Agent": USER_AGENT,
                                "Referer": f"{self.domain}/",
                                "Origin": self.domain,
                                "Accept": "*/*"
                            }
                        }
                    }
                })

        except Exception as e:
            logger.error(f"Stream error: {e}")

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
    response = JSONResponse(content=data)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


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
        "version": "1.4.0",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via FastAPI",
        "logo": ADDON_LOGO,
        "resources": ["stream", "meta", "catalog"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": False
        }
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)

        async with AsyncSession(
            impersonate=IMPERSONATE,
            timeout=20
        ) as client:
            data = await extractor.get_streams(id, client)

        if not data:
            data = {"streams": []}

        return respond_with(data)

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
