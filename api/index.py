import logging
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

load_dotenv()

ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
SOURCE_URL = os.getenv("SOURCE_URL", "https://vidsrc-embed.ru/embed")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
IMPERSONATE = os.getenv("IMPERSONATE", "chrome124")
ADDON_PREFIX = os.getenv("ADDON_PREFIX", "U0MQ")
ADDON_ID = os.getenv("ADDON_ID", "org.stremio.mammamia.ufo")
PORT = int(os.getenv("PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
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

app = FastAPI(title=ADDON_NAME)
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


def respond_json(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def get_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def manifest_url(request: Request) -> str:
    return f"{get_base_url(request)}/{ADDON_PREFIX}/manifest.json"


def install_url(request: Request) -> str:
    return f"stremio://{manifest_url(request).replace('https://', '').replace('http://', '')}"


def random_ua() -> str:
    return random.choice(USER_AGENTS)


def get_sec_ch_ua(user_agent: str) -> str:
    if "Chrome" in user_agent and "Edg" in user_agent:
        return '"Chromium";v="128", "Not;A=Brand";v="24", "Microsoft Edge";v="128"'
    if "Chrome" in user_agent and "Edg" not in user_agent:
        return '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"'
    return ""


def get_sec_ch_ua_platform(user_agent: str) -> str:
    if "Windows" in user_agent:
        return '"Windows"'
    if "Macintosh" in user_agent or "Mac OS X" in user_agent:
        return '"macOS"'
    if "Linux" in user_agent:
        return '"Linux"'
    return '"Windows"'


def randomized_headers(base_dom: str, fetch_dest: str = "script") -> Dict[str, str]:
    ua = random_ua()
    headers: Dict[str, str] = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "priority": "u=1",
        "sec-ch-ua-mobile": "?0",
        "sec-fetch-dest": fetch_dest,
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "same-origin",
        "Referer": f"{base_dom}/",
        "Referrer-Policy": "origin",
        "User-Agent": ua,
    }
    sec_ch_ua = get_sec_ch_ua(ua)
    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
        headers["sec-ch-ua-platform"] = get_sec_ch_ua_platform(ua)
    return headers


def parse_stremio_id(stremio_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    if stremio_id.startswith("tmdb:"):
        parts = stremio_id.split(":")
        content_id = parts[1] if len(parts) > 1 else ""
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
    return f"{SOURCE_URL}/tv/{content_id}/{season or '1'}-{episode or '1'}"


async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        r = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it-IT"},
            timeout=10,
            impersonate=IMPERSONATE,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("movie_results"):
            return data["movie_results"][0].get("id")
        if data.get("tv_results"):
            return data["tv_results"][0].get("id")
        return None
    except Exception as e:
        logger.error(f"TMDB imdb conversion error: {e}")
        return None


async def get_media_title(client: AsyncSession, stremio_id: str, content_type: str) -> str:
    try:
        content_id, season, episode = parse_stremio_id(stremio_id)
        tmdb_id = await get_tmdb_id_from_imdb(content_id, client) if content_id.startswith("tt") else int(content_id)
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}
        if content_type == "movie":
            r = await client.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}", params=params, timeout=10, impersonate=IMPERSONATE)
            return r.json().get("title", f"Film {tmdb_id}") if r.status_code == 200 else f"Film {tmdb_id}"
        r = await client.get(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}",
            params=params,
            timeout=10,
            impersonate=IMPERSONATE,
        )
        return r.json().get("name", f"Episodio {episode}") if r.status_code == 200 else f"Episodio {episode}"
    except Exception as e:
        logger.error(f"TMDB title error: {e}")
        return "Unknown"


def parse_hls_master(master_playlist_content: str, base_url: str) -> List[Dict[str, Any]]:
    qualities: List[Dict[str, Any]] = []
    lines = master_playlist_content.splitlines()
    for i, line in enumerate(lines):
        if "#EXT-X-STREAM-INF:" not in line:
            continue
        attrs = line.split(":", 1)[1] if ":" in line else ""
        bw_match = re.search(r"BANDWIDTH=(\d+)", attrs)
        res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", attrs)
        codecs_match = re.search(r'CODECS="([^"]+)"', attrs)
        fr_match = re.search(r"FRAME-RATE=([\d.]+)", attrs)
        bandwidth = int(bw_match.group(1)) if bw_match else 0
        resolution = f"{res_match.group(1)}x{res_match.group(2)}" if res_match else None
        height = int(res_match.group(2)) if res_match else 0
        codecs = codecs_match.group(1) if codecs_match else None
        frame_rate = float(fr_match.group(1)) if fr_match else None
        playlist_uri = None
        for j in range(i + 1, min(i + 5, len(lines))):
            candidate = lines[j].strip()
            if candidate and not candidate.startswith("#"):
                playlist_uri = candidate
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
            "title": title,
        })
    qualities.sort(key=lambda x: x.get("bandwidth", 0), reverse=True)
    return qualities


async def fetch_and_parse_hls(url: str, client: AsyncSession, referer: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        headers = {"User-Agent": random_ua()}
        if referer:
            headers["Referer"] = f"{referer}/" if not referer.endswith("/") else referer
        r = await client.get(url, headers=headers, timeout=15, impersonate=IMPERSONATE)
        if r.status_code != 200:
            return None
        content = r.text
        if "#EXT-X-STREAM-INF" not in content:
            return None
        return {"masterUrl": url, "qualities": parse_hls_master(content, url)}
    except Exception as e:
        logger.warning(f"HLS parse failed: {e}")
        return None


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
            parsed = urlparse(base)
            if parsed.scheme and parsed.netloc:
                self.base_dom = f"{parsed.scheme}://{parsed.netloc}"
        for server in soup.select(".serversList .server"):
            servers.append({"name": server.get_text(strip=True), "dataHash": server.get("data-hash")})
        return servers, title

    async def rcp_grabber(self, html: str) -> Optional[str]:
        m = re.search(r"src:\s*'([^']*)'", html)
        return m.group(1) if m else None

    async def prorcp_handler(self, prorcp: str, client: AsyncSession) -> Optional[str]:
        try:
            r = await client.get(
                f"{self.base_dom}/prorcp/{prorcp}",
                headers=randomized_headers(self.base_dom),
                timeout=20,
                impersonate=IMPERSONATE,
            )
            if r.status_code != 200:
                return None
            m = re.search(r"file:\s*'([^']*)'", r.text)
            return m.group(1) if m else None
        except Exception as e:
            logger.warning(f"prorcp failed: {e}")
            return None

    async def extract(self, stremio_id: str, content_type: str, client: AsyncSession) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        embed_url = build_embed_url(stremio_id, content_type)
        title = await get_media_title(client, stremio_id, content_type)
        embed = await client.get(embed_url, headers=randomized_headers(self.base_dom), timeout=20, impersonate=IMPERSONATE)
        if embed.status_code != 200:
            logger.warning(f"embed status {embed.status_code}")
            return []
        servers, _ = await self.servers_load(embed.text)
        for server in servers:
            data_hash = server.get("dataHash")
            if not data_hash:
                continue
            try:
                headers = randomized_headers(self.base_dom)
                headers["Sec-Fetch-Dest"] = ""
                r = await client.get(f"{self.base_dom}/rcp/{data_hash}", headers=headers, timeout=20, impersonate=IMPERSONATE)
                if r.status_code != 200:
                    continue
                rcp_data = await self.rcp_grabber(r.text)
                if not rcp_data or not rcp_data.startswith("/prorcp/"):
                    continue
                stream_url = await self.prorcp_handler(rcp_data.replace("/prorcp/", ""), client)
                if not stream_url:
                    continue
                hls_data = await fetch_and_parse_hls(stream_url, client, self.base_dom)
                results.append({
                    "name": title,
                    "stream": stream_url,
                    "referer": self.base_dom,
                    "hlsData": hls_data,
                    "server": server.get("name") or "VidSRC",
                })
            except Exception as e:
                logger.warning(f"server extraction failed: {e}")
        return results


extractor = StremsrcStyleExtractor()


def make_proxy_playlist_url(request: Request, target_url: str, referer: str) -> str:
    base = get_base_url(request)
    return f"{base}/{ADDON_PREFIX}/proxy/playlist.m3u8?url={quote(target_url, safe='')}&referer={quote(referer, safe='')}"


def make_proxy_segment_url(request: Request, target_url: str, referer: str) -> str:
    base = get_base_url(request)
    return f"{base}/{ADDON_PREFIX}/proxy/segment?url={quote(target_url, safe='')}&referer={quote(referer, safe='')}"


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    murl = manifest_url(request)
    surl = install_url(request)
    html = f"""
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{ADDON_NAME}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; line-height: 1.5; }}
    code, pre {{ background: #f4f4f4; padding: 8px; border-radius: 6px; overflow:auto; }}
    a.button {{ display:inline-block; padding:12px 16px; background:#0b6; color:#fff; text-decoration:none; border-radius:8px; }}
    .card {{ border:1px solid #ddd; border-radius:12px; padding:20px; margin:20px 0; }}
  </style>
</head>
<body>
  <h1>{ADDON_NAME}</h1>
  <div class="card">
    <p><strong>Manifest URL corretto per Stremio:</strong></p>
    <pre>{murl}</pre>
    <p>Questo è il link da incollare in Stremio per installare l'addon.</p>
    <p><strong>Deep link Stremio:</strong></p>
    <pre>{surl}</pre>
    <p><a class="button" href="{surl}">Installa in Stremio</a></p>
  </div>
  <div class="card">
    <p><strong>Endpoint stream:</strong></p>
    <pre>{get_base_url(request)}/{ADDON_PREFIX}/stream/{{movie|series}}/{{id}}.json</pre>
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get(f"/{ADDON_PREFIX}/manifest.json")
async def manifest(request: Request):
    return respond_json({
        "id": ADDON_ID,
        "version": "2.0.0",
        "name": ADDON_NAME,
        "description": "VidSRC-style extractor with HLS proxy via FastAPI",
        "logo": ADDON_LOGO,
        "resources": ["stream", "meta", "catalog"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt", "tmdb"],
        "behaviorHints": {"configurable": False},
    })


@app.get(f"/{ADDON_PREFIX}/stream/{{content_type}}/{{id}}.json")
@limiter.limit("10/second")
async def streams(request: Request, content_type: str, id: str):
    try:
        if content_type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        async with AsyncSession(impersonate=IMPERSONATE, timeout=25) as client:
            extracted = await extractor.extract(id, content_type, client)
        streams: List[Dict[str, Any]] = []
        for st in extracted:
            referer = st["referer"]
            stream_url = st["stream"]
            proxied_master = make_proxy_playlist_url(request, stream_url, referer)
            hls_data = st.get("hlsData")
            if hls_data and hls_data.get("qualities"):
                streams.append({
                    "name": "🛸 Auto",
                    "title": f"{st['name']} - {st['server']} Auto",
                    "url": proxied_master,
                    "behaviorHints": {"notWebReady": True, "bingeGroup": "ufo-stremsrc-proxy"},
                })
                for q in hls_data["qualities"]:
                    streams.append({
                        "name": f"🛸 {q['title']}",
                        "title": f"{st['name']} - {st['server']} {q['title']}",
                        "url": make_proxy_playlist_url(request, q['url'], referer),
                        "behaviorHints": {"notWebReady": True, "bingeGroup": "ufo-stremsrc-proxy"},
                    })
            else:
                streams.append({
                    "name": "🛸 Stream",
                    "title": f"{st['name']} - {st['server']}",
                    "url": proxied_master,
                    "behaviorHints": {"notWebReady": True, "bingeGroup": "ufo-stremsrc-proxy"},
                })
        return respond_json({"streams": streams})
    except Exception as e:
        logger.error(f"stream endpoint error: {e}")
        return respond_json({"streams": []})


@app.get(f"/{ADDON_PREFIX}/meta/{{content_type}}/{{id}}.json")
async def meta(content_type: str, id: str):
    return respond_json({"meta": {"id": id, "type": content_type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get(f"/{ADDON_PREFIX}/catalog/{{content_type}}/{{id}}.json")
async def catalog(content_type: str, id: str):
    return respond_json({"metas": []})


@app.get(f"/{ADDON_PREFIX}/proxy/playlist.m3u8")
async def proxy_playlist(url: str = Query(...), referer: str = Query(...)):
    try:
        target_url = unquote(url)
        referer_url = unquote(referer)
        headers = {"User-Agent": random_ua(), "Referer": f"{referer_url}/" if not referer_url.endswith("/") else referer_url}
        async with AsyncSession(impersonate=IMPERSONATE, timeout=30) as client:
            r = await client.get(target_url, headers=headers, timeout=25, impersonate=IMPERSONATE)
        if r.status_code != 200:
            return PlainTextResponse("", status_code=404)
        content = r.text
        if "#EXTM3U" not in content:
            return PlainTextResponse(content, media_type="application/vnd.apple.mpegurl")
        out_lines: List[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                out_lines.append(line)
                continue
            absolute = stripped if stripped.startswith("http") else urljoin(target_url, stripped)
            proxied = f"/{ADDON_PREFIX}/proxy/segment?url={quote(absolute, safe='')}&referer={quote(referer_url, safe='')}"
            out_lines.append(proxied)
        body = "\n".join(out_lines)
        return Response(content=body, media_type="application/vnd.apple.mpegurl", headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"proxy playlist error: {e}")
        return PlainTextResponse("", status_code=500)


@app.get(f"/{ADDON_PREFIX}/proxy/segment")
async def proxy_segment(url: str = Query(...), referer: str = Query(...)):
    try:
        target_url = unquote(url)
        referer_url = unquote(referer)
        headers = {"User-Agent": random_ua(), "Referer": f"{referer_url}/" if not referer_url.endswith("/") else referer_url}
        async with AsyncSession(impersonate=IMPERSONATE, timeout=30) as client:
            r = await client.get(target_url, headers=headers, timeout=25, impersonate=IMPERSONATE)
        content_type = r.headers.get("content-type", "application/octet-stream")
        if "application/vnd.apple.mpegurl" in content_type or ".m3u8" in target_url:
            content = r.text
            out_lines: List[str] = []
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    out_lines.append(line)
                    continue
                absolute = stripped if stripped.startswith("http") else urljoin(target_url, stripped)
                proxied = f"/{ADDON_PREFIX}/proxy/segment?url={quote(absolute, safe='')}&referer={quote(referer_url, safe='')}"
                out_lines.append(proxied)
            return Response(content="\n".join(out_lines), media_type="application/vnd.apple.mpegurl", headers={"Access-Control-Allow-Origin": "*"})
        return Response(content=r.content, media_type=content_type, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"proxy segment error: {e}")
        return PlainTextResponse("", status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
