import json
import logging
import re
import os
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urljoin
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup, SoupStrainer
from fake_headers import Headers
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================

ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

CONFIG = {
    "Siti": {
        "StreamingCommunity": {
            "url": "https://vixsrc.to", 
            "enabled": "1"
        },
        "CB01": {
            "url": "https://cb01.uno",
            "enabled": "1"
        }
    }
}

# LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# UTILITIES & TMDB HELPERS
# ============================================================================

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
        if response.status_code == 200:
            data = response.json()
            if data.get('movie_results'): return data['movie_results'][0].get('id')
            if data.get('tv_results'): return data['tv_results'][0].get('id')
        return None
    except Exception as e:
        logger.error(f"❌ Error converting IMDb ID: {e}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    """Recupera il titolo formattato da TMDB."""
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        
        if not is_series:
            # --- FILM ---
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            # --- SERIE TV ---
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            
            if ep_resp.status_code == 200:
                ep_data = ep_resp.json()
                return ep_data.get('name', f"Episodio {episode}")
            
            return f"Episodio {episode}"
        
    except Exception as e:
        logger.error(f"❌ Errore recupero titolo TMDB: {e}")
        if is_series: return f"Episodio {episode}"
        return "Film"

# ============================================================================
# EXTRACTORS
# ============================================================================

class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        """
        Estrae l'URL della Master Playlist da VixSrc.
        """
        try:
            logger.info(f"🔍 [VixSrc] Fetching: {link}")
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            
            video_data = None
            for script in scripts:
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    break
            
            if not video_data:
                return []

            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]):
                return []

            token = token_match.group(1)
            expires = expires_match.group(1)
            server_url = url_match.group(1)
            
            separator = "&" if "?" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            
            # Parametri opzionali
            if "?b=1" in server_url and "b=1" not in final_url: final_url += "&b=1"
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            # Fix estensione
            if ".m3u8" not in final_url: 
                if "?" in final_url: 
                    base, params = final_url.split("?", 1) 
                    if not base.endswith(".m3u8"): final_url = f"{base}.m3u8?{params}"
                else: 
                    final_url += ".m3u8"

            # --- Analisi Metadata (Solo per etichetta) ---
            detected_quality = "Auto"
            max_height = 0
            try:
                m3u8_res = await client.get(final_url, headers=headers, timeout=6)
                if m3u8_res.status_code == 200:
                    lines = m3u8_res.text.splitlines()
                    for line in lines:
                        if "RESOLUTION=" in line:
                            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                            if res_match:
                                height = int(res_match.group(2))
                                if height > max_height:
                                    max_height = height
                    
                    if max_height > 0:
                        detected_quality = f"{max_height}p"
                
            except Exception as e:
                logger.warning(f"⚠️ [VixSrc] Impossibile analizzare metadata m3u8: {e}")

            if max_height == 0 and "window.canPlayFHD = true" in video_data:
                detected_quality = "1080p"
            elif max_height == 0:
                detected_quality = "720p"

            logger.info(f"✅ [VixSrc] URL Master generato. Qualità: {detected_quality}")
            
            return [{
                "quality": detected_quality,
                "url": final_url,
                "height": max_height
            }]

        except Exception as e:
            logger.error(f"❌ [VixSrc] Extractor Error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        streams = {'streams': []}
        try:
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

            tmdb_id = None
            if content_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id: return streams
            else:
                try: tmdb_id = int(content_id)
                except ValueError: return streams

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            
            results = await self.extract_vixcloud_url(url, client)
            
            for res in results:
                streams['streams'].append({
                    "name": f"🛸 VixSrc {res['quality']}", 
                    "title": media_title,
                    "url": res['url'],
                    "behaviorHints": {
                        "proxyHeaders": {"request": {"user-agent": User_Agent}},
                        "notWebReady": True,
                        "bingeGroup": "vixsrc"
                    }
                })

        except Exception as e:
            logger.error(f"❌ [VixSrc] Stream Error: {e}")
        
        return streams


class CB01Extractor:
    """Estrattore CB01 con parsing completo e fix parametri"""
    def __init__(self):
        self.domain = CONFIG['Siti']['CB01']['url']
        self.random_headers = Headers()

    async def get_stayonline(self, link: str, client: AsyncSession) -> Optional[str]:
        """Bypass StayOnline per ottenere URL reale"""
        try:
            headers = {
                'origin': 'https://stayonline.pro',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 OPR/111.0.0.0',
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {'id': link.split("/")[-2], 'ref': ''}
            response = await client.post('https://stayonline.pro/ajax/linkEmbedView.php',
                                        headers=headers, data=data, timeout=10)
            real_url = response.json()['data']['value']
            logger.info(f"✅ [CB01] StayOnline bypassed: {real_url[:80]}...")
            return real_url
        except Exception as e:
            logger.warning(f"⚠️ [CB01] StayOnline error: {e}")
            return None

    async def get_maxstream(self, link: str, streams: Dict, client: AsyncSession, media_title: str = "Film") -> Dict:
        """Estrae stream da maxstream/uprot"""
        try:
            # Se è stayonline, bypass prima
            if "stayonline" in link:
                link = await self.get_stayonline(link, client)
                if not link:
                    return streams
            
            logger.info(f"📺 [CB01] Extracting from: {link[:80]}...")
            
            # Aggiunge lo stream con note sulla captcha se necessario
            streams['streams'].append({
                "name": "📺 CB01",
                "title": f"{media_title}\n(Potrebbe richiedere Captcha)",
                "url": link,
                "behaviorHints": {
                    "proxyHeaders": {"request": {"user-agent": User_Agent}},
                    "notWebReady": True,
                    "bingeGroup": "cb01"
                }
            })
            return streams
        except Exception as e:
            logger.error(f"❌ [CB01] Maxstream error: {e}")
            return streams

    async def movie_redirect_url(self, link: str, client: AsyncSession, streams: Dict, media_title: str = "") -> Dict:
        """Estrae video da pagina film CB01"""
        try:
            logger.info(f"🔗 [CB01] Parsing movie page: {link}")
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            
            response = await client.get(link, headers=headers, allow_redirects=True, timeout=15)
            
            soup = BeautifulSoup(response.text, "lxml")
            
            # Cerca i div con data-src (contenitori video)
            redirect_url = soup.find("div", id="iframen2")
            redirect_url_2 = soup.find("div", id="iframen1")
            
            if redirect_url and redirect_url.get("data-src"):
                real_url = redirect_url.get("data-src")
                streams = await self.get_maxstream(real_url, streams, client, media_title)
            elif redirect_url_2 and redirect_url_2.get("data-src"):
                real_url = redirect_url_2.get("data-src")
                streams = await self.get_maxstream(real_url, streams, client, media_title)
            else:
                logger.warning(f"⚠️ [CB01] Nessun video trovato nella pagina")
                # Fallback: aggiungi il link della pagina come stream
                streams['streams'].append({
                    "name": "📺 CB01 (Page Link)",
                    "title": "Apri su CB01 per vedere il film",
                    "url": link,
                    "behaviorHints": {
                        "proxyHeaders": {"request": {"user-agent": User_Agent}},
                        "bingeGroup": "cb01"
                    }
                })
            
            logger.info(f"✅ [CB01] Movie parsed successfully")
            return streams
        except Exception as e:
            logger.error(f"❌ [CB01] Movie redirect error: {e}")
            return streams

    async def series_redirect_url(self, link: str, season: str, episode: str, client: AsyncSession, streams: Dict, media_title: str = "") -> Dict:
        """Estrae video da pagina serie CB01"""
        try:
            episode = episode.zfill(2)
            logger.info(f"🔗 [CB01] Parsing series page: {link} (S{season}E{episode})")
            
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            
            response = await client.get(link, headers=headers, allow_redirects=True, timeout=15)
            soup = BeautifulSoup(response.text, "lxml")
            
            # Cerca le stagioni
            seasons_text = soup.find_all('div', class_='sp-head')
            
            for season_text in seasons_text:
                text = season_text.text
                
                # Controlla se è la stagione giusta
                if f'STAGIONE' in text and f'{season}' in text:
                    # Cerca l'episodio
                    season_div = season_text.find_next('div', class_='sp-body')
                    if season_div:
                        ep_pattern = re.compile(rf"(S{season}E{episode}|{season}x{episode})", re.IGNORECASE)
                        ep_match = ep_pattern.search(season_div.text)
                        if ep_match:
                            # Cerca il link dopo il match
                            links = season_div.find_all('a')
                            if links:
                                video_url = links[0].get('href')
                                if video_url:
                                    streams = await self.get_maxstream(video_url, streams, client, media_title)
                                    logger.info(f"✅ [CB01] Episode found and extracted")
                                    return streams
            
            logger.warning(f"⚠️ [CB01] Episode not found")
            return streams
        except Exception as e:
            logger.error(f"❌ [CB01] Series redirect error: {e}")
            return streams

    async def search_movie(self, showname: str, date: str, client: AsyncSession) -> Optional[str]:
        """Cerca un film su CB01"""
        try:
            showname_clean = showname.replace(" ", "+").replace("ò", "o").replace("è", "e").replace("à", "a").replace("ù", "u").replace("ì", "i")
            headers = self.random_headers.generate()
            headers['Referer'] = f'{self.domain}/'
            
            query = f'{self.domain}/?s={showname_clean}'
            logger.info(f"🔍 [CB01] Searching movie: {showname} ({date})")
            
            response = await client.get(query, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(f"⚠️ [CB01] Failed to fetch search: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'lxml', parse_only=SoupStrainer('div', class_='card-content'))
            cards = soup.find_all('div', class_='card-content')
            
            year_pattern = re.compile(r'(19|20)\d{2}')
            
            for card in cards:
                try:
                    link_tag = card.find('h3', class_='card-title')
                    if not link_tag:
                        continue
                    link_a = link_tag.find('a')
                    if not link_a:
                        continue
                    
                    href = link_a.get('href')
                    if not href:
                        continue
                    
                    date_text = href.split("/")[-2]
                    
                    match = year_pattern.search(date_text)
                    if match and match.group(0) == date:
                        logger.info(f"✅ [CB01] Found movie: {href}")
                        return href
                except Exception as e:
                    logger.debug(f"⚠️ [CB01] Error parsing card: {e}")
                    continue
            
            logger.warning(f"⚠️ [CB01] No movie found for: {showname} ({date})")
            return None
        except Exception as e:
            logger.error(f"❌ [CB01] Search error: {e}")
            return None

    async def search_series(self, showname: str, date: str, client: AsyncSession) -> Optional[str]:
        """Cerca una serie su CB01"""
        try:
            showname_clean = showname.replace(" ", "+")
            headers = self.random_headers.generate()
            headers['Referer'] = f'{self.domain}/serietv/'
            
            query = f'{self.domain}/serietv/?s={showname_clean}'
            logger.info(f"🔍 [CB01] Searching series: {showname} ({date})")
            
            response = await client.get(query, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(f"⚠️ [CB01] Failed to fetch search: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'lxml', parse_only=SoupStrainer('div', class_='card-content'))
            cards = soup.find_all('div', class_='card-content')
            
            year_pattern = re.compile(r'(19|20)\d{2}')
            
            for card in cards:
                try:
                    link_tag = card.find('h3', class_='card-title')
                    if not link_tag:
                        continue
                    link_a = link_tag.find('a')
                    if not link_a:
                        continue
                    
                    href = link_a.get('href')
                    if not href:
                        continue
                    
                    date_span = card.find('span', style=re.compile('color'))
                    
                    if date_span:
                        date_text = date_span.text
                        match = year_pattern.search(date_text)
                        if match:
                            year = match.group(0)
                            if abs(int(year) - int(date)) <= 1:
                                logger.info(f"✅ [CB01] Found series: {href}")
                                return href
                except Exception as e:
                    logger.debug(f"⚠️ [CB01] Error parsing card: {e}")
                    continue
            
            logger.warning(f"⚠️ [CB01] No series found for: {showname} ({date})")
            return None
        except Exception as e:
            logger.error(f"❌ [CB01] Search error: {e}")
            return None

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        """Estrae stream da CB01"""
        streams = {'streams': []}
        try:
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

            tmdb_id = None
            if content_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id: return streams
            else:
                try: tmdb_id = int(content_id)
                except ValueError: return streams

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            # Recupera metadati TMDB
            title_response = await client.get(
                f"https://api.themoviedb.org/3/{'tv' if is_series else 'movie'}/{tmdb_id}",
                params={"api_key": TMDB_API_KEY, "language": "it"},
                timeout=5
            )
            
            if title_response.status_code != 200:
                logger.warning(f"⚠️ [CB01] Could not fetch TMDB data")
                return streams
            
            tmdb_data = title_response.json()
            title = tmdb_data.get('name' if is_series else 'title', '')
            date = (tmdb_data.get('first_air_date') or tmdb_data.get('release_date') or '').split('-')[0]
            
            if not title or not date:
                logger.warning(f"⚠️ [CB01] Missing title or date")
                return streams

            # Ricerca su CB01
            if is_series:
                link = await self.search_series(title, date, client)
                if link:
                    streams = await self.series_redirect_url(link, season, episode, client, streams, media_title)
            else:
                link = await self.search_movie(title, date, client)
                if link:
                    streams = await self.movie_redirect_url(link, client, streams, media_title)
            
            if streams['streams']:
                logger.info(f"✅ [CB01] {len(streams['streams'])} stream(s) found for {media_title}")

        except Exception as e:
            logger.error(f"❌ [CB01] Stream Error: {e}")
        
        return streams

# ============================================================================
# FASTAPI SETUP
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

vixsrc_extractor = StreamingCommunityExtractor()
cb01_extractor = CB01Extractor()

def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

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

@app.head("/")
async def root_head():
    return JSONResponse(content={"status": "ok"})

@app.get("/U0MQ/manifest.json")
async def manifest():
    config = {
        "id": "org.stremio.mammamia.ufo",
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "VixSrc + CB01 Streams via Vercel",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    }
    return respond_with(config)

@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]: raise HTTPException(status_code=404)
        
        async with AsyncSession() as client:
            # Priorità: VixSrc prima, CB01 come fallback
            logger.info(f"🎬 Fetching streams for {type}/{id}")
            streams_data = await vixsrc_extractor.get_streams(id, client)
            
            # Se VixSrc non ha trovato stream, prova CB01
            if not streams_data.get('streams'):
                logger.info(f"ℹ️ VixSrc non ha trovato stream, provo CB01...")
                streams_data = await cb01_extractor.get_streams(id, client)
            else:
                # Se VixSrc ha trovato stream, aggiungi CB01 come fallback
                cb01_streams = await cb01_extractor.get_streams(id, client)
                if cb01_streams.get('streams'):
                    streams_data['streams'].extend(cb01_streams['streams'])
                    logger.info(f"✅ Aggiunto CB01 come fallback")

            if not streams_data: 
                streams_data = {"streams": []}
                
            logger.info(f"📊 Total streams found: {len(streams_data.get('streams', []))}")
        
        return respond_with(streams_data)
    except Exception as e:
        logger.error(f"❌ Stream endpoint error: {e}")
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
