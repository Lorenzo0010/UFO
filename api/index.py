import logging
import os
import re
from typing import Any, Dict, Optional

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

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
IMPERSONATE = "chrome110"


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
            params={
                "external_source": "imdb_id",
                "api_key": TMDB_API_KEY,
                "language": "it",
            },
            timeout=15,
            impersonate=IMPERSONATE,
        )
        if response.status_code != 200:
            logger.warning(f"TMDB lookup failed for {imdb_id}: {response.status_code}")
            return None
        data = response.json()
        if data.get("movie_results"):
            return data["movie_results"][0].get("id")
        if data.get("tv_results"):
            return data["tv_results"][0].get("id")
        return None
    except Exception as e:
        logger.error(f"❌ Error converting IMDb ID: {e}")
        return None


class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG["Siti"]["StreamingCommunity"]["url"]
        self.random_headers = Headers()

    def build_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        headers = self.random_headers.generate()
        headers["User-Agent"] = USER_AGENT
        headers["Referer"] = referer or f"{self.domain}/"
        headers["Origin"] = self.domain
        return headers

    def parse_stream_data(self, html: str) -> Optional[Dict[str, str]]:
        """
        Cerca window.masterPlaylist con questa struttura:
            window.masterPlaylist = {
                params: {
                    'token': 'abc...',
                    'expires': '123...',
                },
                url: 'https://vixsrc.to/playlist/XXXXXX',
            }
        """
        soup = BeautifulSoup(html, "lxml")
        scripts = soup.find_all("script")

        for script in scripts:
            content = script.string or script.text or ""
            if "masterPlaylist" not in content:
                continue

            token_match = re.search(r"['\"]token['\"]\s*:\s*['\"]([^'\"]+)['\"]", content)
            expires_match = re.search(r"['\"]expires['\"]\s*:\s*['\"]([\d]+)['\"]", content)
            # URL dentro window.masterPlaylist - cerchiamo dopo la keyword
            url_match = re.search(r"masterPlaylist\s*=\s*\{[^}]*url\s*:\s*['\"]([^'\"]+)['\"]", content, re.DOTALL)
            if not url_match:
                # fallback: qualsiasi url: 'https://...' nel blocco
                url_match = re.search(r"url\s*:\s*['\"]([^'\"]+)['\"]", content)

            logger.info(
                f"📜 masterPlaylist block → token:{bool(token_match)} "
                f"expires:{bool(expires_match)} url:{bool(url_match)}"
            )

            if token_match and expires_match and url_match:
                result = {
                    "token": token_match.group(1),
                    "expires": expires_match.group(1),
                    "url": url_match.group(1),
                    "fhd": "1" if "canPlayFHD" in content else "0",
                }
                logger.info(f"🎯 Parsed: url={result['url']} token={result['token'][:8]}... fhd={result['fhd']}")
                return result

        logger.warning("⚠️ masterPlaylist block not found in any script")
        return None

    def build_candidate_url(self, data: Dict[str, str]) -> str:
        """
        Costruisce:
            https://vixsrc.to/playlist/265527?token=...&expires=...&h=1
        """
        playlist_url = data["url"]
        separator = "&" if "?" in playlist_url else "?"
        url = f"{playlist_url}{separator}token={data['token']}&expires={data['expires']}"
        if data.get("fhd") == "1" and "h=1" not in url:
            url += "&h=1"
        return url

    async def resolve_final_manifest_url(
        self, candidate_url: str, page_url: str, client: AsyncSession
    ) -> Optional[str]:
        """
        Segue i redirect di /playlist/XXXXX e restituisce l'URL finale .m3u8
        """
        try:
            response = await client.get(
                candidate_url,
                headers=self.build_headers(page_url),
                timeout=20,
                allow_redirects=True,
                impersonate=IMPERSONATE,
            )

            final_url = str(response.url)
            content_type = response.headers.get("content-type", "").lower()
            body_start = ""
            try:
                body_start = response.text[:300]
            except Exception:
                pass

            logger.info(f"🧪 Candidate: {candidate_url}")
            logger.info(f"✅ Resolved: {final_url}")
            logger.info(f"📄 Content-Type: {content_type}")
            logger.info(f"📝 Body preview: {body_start[:120]}")

            if ".m3u8" in final_url:
                return final_url
            if "application/vnd.apple.mpegurl" in content_type:
                return final_url
            if "application/x-mpegurl" in content_type:
                return final_url
            if "#EXTM3U" in body_start:
                return final_url

            logger.warning(
                f"⚠️ Not a valid HLS manifest. "
                f"status={response.status_code} CT={content_type} body={body_start[:80]}"
            )
            return None

        except Exception as e:
            logger.error(f"❌ Error resolving manifest URL: {e}")
            return None

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> Optional[str]:
        try:
            logger.info(f"🔍 Fetching page: {link}")
            response = await client.get(
                link,
                headers=self.build_headers(),
                timeout=20,
                impersonate=IMPERSONATE,
            )

            logger.info(
                f"📡 Page status: {response.status_code} | "
                f"CT: {response.headers.get('content-type', '?')} | "
                f"len: {len(response.text)}"
            )

            if response.status_code != 200:
                logger.warning(f"Page fetch failed: {response.status_code} - {link}")
                return None

            parsed = self.parse_stream_data(response.text)
            if not parsed:
                return None

            candidate_url = self.build_candidate_url(parsed)
            return await self.resolve_final_manifest_url(candidate_url, link, client)

        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}")
            return None

    async def get_streams(
        self, id: str, content_type: str, client: AsyncSession
    ) -> Dict[str, list]:
        streams = {"streams": []}
        try:
            content_id = clean_id(id)
            is_series = ":" in id
            season = None
            episode = None

            if is_series:
                parts = id.split(":")
                if len(parts) >= 3:
                    content_id = parts[0]
                    season = parts[1]
                    episode = parts[2]

            if content_id.startswith("tt"):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    logger.warning(f"No TMDB id found for IMDb id {content_id}")
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                except ValueError:
                    logger.warning(f"Invalid content id: {content_id}")
                    return streams

            if is_series and content_type == "series":
                if season is None or episode is None:
                    return streams
                page_url = f"{self.domain}/tv/{tmdb_id}/{season}/{episode}/"
                filename = f"{content_id}-S{int(season):02d}E{int(episode):02d}.m3u8"
            elif content_type == "movie":
                page_url = f"{self.domain}/movie/{tmdb_id}/"
                filename = f"{content_id}.m3u8"
            else:
                return streams

            stream_url = await self.extract_vixcloud_url(page_url, client)
            if not stream_url:
                logger.warning(f"No playable stream found for {id}")
                return streams

            streams["streams"].append({
                "name": "🛸 UFO",
                "description": self.domain,
                "title": "VixSrc direct stream",
                "url": stream_url,
                "filename": filename,
                "behaviorHints": {
                    "notWebReady": True,
                    "bingeGroup": "streamingcommunity",
                    "proxyHeaders": {
                        "request": {
                            "User-Agent": USER_AGENT,
                            "Referer": page_url,
                            "Origin": self.domain,
                        }
                    }
                }
            })

            logger.info(f"✅ Stream ready for {id}: {stream_url}")
            return streams

        except Exception as e:
            logger.error(f"❌ Stream Error: {e}")
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
        "version": "1.6.0",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Vercel",
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
        async with AsyncSession() as client:
            data = await extractor.get_streams(id, type, client)
        return respond_with(data or {"streams": []})
    except Exception as e:
        logger.error(f"❌ Route /stream error: {e}")
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


# ============================================================================
# DEBUG - rimuovi in produzione
# ============================================================================
@app.get("/U0MQ/debug/movie/{tmdb_id}")
async def debug_movie(tmdb_id: str):
    try:
        async with AsyncSession() as client:
            url = f"https://vixsrc.to/movie/{tmdb_id}/"
            response = await client.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://vixsrc.to/",
                    "Origin": "https://vixsrc.to"
                },
                timeout=20,
                impersonate=IMPERSONATE,
            )
            soup = BeautifulSoup(response.text, "lxml")
            scripts = []
            for i, s in enumerate(soup.find_all("script")):
                content = s.string or s.text or ""
                if len(content) > 20:
                    scripts.append({
                        "index": i,
                        "length": len(content),
                        "has_masterPlaylist": "masterPlaylist" in content,
                        "has_token": "token" in content,
                        "has_expires": "expires" in content,
                        "preview": content[:500]
                    })
            return respond_with({
                "status_code": response.status_code,
                "final_url": str(response.url),
                "content_type": response.headers.get("content-type", ""),
                "page_length": len(response.text),
                "scripts": scripts[:10],
            })
    except Exception as e:
        return respond_with({"error": str(e)})


@app.get("/U0MQ/debug/stream/{tmdb_id}")
async def debug_stream(tmdb_id: str):
    """Mostra l'URL finale .m3u8 che verrebbe restituito a Stremio"""
    try:
        async with AsyncSession() as client:
            extractor_inst = StreamingCommunityExtractor()
            url = await extractor_inst.extract_vixcloud_url(
                f"https://vixsrc.to/movie/{tmdb_id}/", client
            )
            return respond_with({
                "tmdb_id": tmdb_id,
                "resolved_stream_url": url,
                "valid": url is not None
            })
    except Exception as e:
        return respond_with({"error": str(e)})
