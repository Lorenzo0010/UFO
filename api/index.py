import logging
import re
import os
import json
import base64
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urljoin, quote

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
            "enabled": True
        },
        "Guardaserie": {
            "url": "https://guardaserie.tv",
            "enabled": True
        }
    }
}

# LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# UTILITIES: PACKER UNPACKER (Logica da eval.py)
# ============================================================================
class UnpackingError(Exception):
    pass

class Unbaser(object):
    """Functor for a given base. Will efficiently convert strings to natural numbers."""
    ALPHABET = {
        62: "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        95: (" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"),
    }
    def __init__(self, base):
        self.base = base
        if 36 < base < 62:
            if not hasattr(self.ALPHABET, self.ALPHABET[62][:base]):
                self.ALPHABET[base] = self.ALPHABET[62][:base]
        if 2 <= base <= 36:
            self.unbase = lambda string: int(string, base)
        else:
            try:
                self.dictionary = dict((cipher, index) for index, cipher in enumerate(self.ALPHABET[base]))
            except KeyError:
                raise TypeError("Unsupported base encoding.")
            self.unbase = self._dictunbaser

    def __call__(self, string):
        return self.unbase(string)

    def _dictunbaser(self, string):
        ret = 0
        for index, cipher in enumerate(string[::-1]):
            ret += (self.base**index) * self.dictionary[cipher]
        return ret

def detect_packer(source):
    return "eval(function(p,a,c,k,e,d)" in source

def unpack_packer(source):
    """Unpacks P.A.C.K.E.R. packed js code."""
    payload, symtab, radix, count = _filterargs(source)
    if count != len(symtab):
        raise UnpackingError("Malformed p.a.c.k.e.r. symtab.")
    try:
        unbase = Unbaser(radix)
    except TypeError:
        raise UnpackingError("Unknown p.a.c.k.e.r. encoding.")

    def lookup(match):
        word = match.group(0)
        return symtab[unbase(word)] or word

    payload = payload.replace("\\\\", "\\").replace("\\'", "'")
    source = re.sub(r"\b\w+\b", lookup, payload)
    return _replacestrings(source)

def _filterargs(source):
    juicers = [
        (r"}\('(.*)', *(\d+|\[\]), *(\d+), *'(.*)'\.split\('\|'\), *(\d+), *(.*)\)\)"),
        (r"}\('(.*)', *(\d+|\[\]), *(\d+), *'(.*)'\.split\('\|'\)"),
    ]
    for juicer in juicers:
        args = re.search(juicer, source, re.DOTALL)
        if args:
            a = args.groups()
            if a[1] == "[]":
                a = list(a)
                a[1] = 62
                a = tuple(a)
            try:
                return a[0], a[3].split("|"), int(a[1]), int(a[2])
            except ValueError:
                raise UnpackingError("Corrupted p.a.c.k.e.r. data.")
    raise UnpackingError("Could not make sense of p.a.c.k.e.r data")

def _replacestrings(source):
    match = re.search(r'var *(_\w+)\=\["(.*?)"\];', source, re.DOTALL)
    if match:
        varname, strings = match.groups()
        startpoint = len(match.group(0))
        lookup = strings.split('","')
        variable = "%s[%%d]" % varname
        for index, value in enumerate(lookup):
            source = source.replace(variable % index, '"%s"' % value)
        return source[startpoint:]
    return source

# ============================================================================
# TMDB HELPERS
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
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json().get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            if ep_resp.status_code == 200:
                return ep_resp.json().get('name', f"Episodio {episode}")
            return f"Episodio {episode}"
    except Exception as e:
        return "Video"

# ============================================================================
# EXTRACTOR 1: STREAMING COMMUNITY (VixSrc)
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        try:
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200: return []

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            video_data = None
            for script in scripts:
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    break
            
            if not video_data: return []

            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]): return []

            final_url = f"{url_match.group(1)}?token={token_match.group(1)}&expires={expires_match.group(1)}"
            if "?b=1" in url_match.group(1) and "b=1" not in final_url: final_url += "&b=1"
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            if ".m3u8" not in final_url:
                 final_url += ".m3u8"

            detected_quality = "1080p" if "window.canPlayFHD = true" in video_data else "720p"
            return [{"quality": detected_quality, "url": final_url}]

        except Exception as e:
            logger.error(f"❌ SC Extractor Error: {e}")
            return []

    async def get_streams(self, tmdb_id: int, is_series: bool, season: str, episode: str, client: AsyncSession) -> List[Dict]:
        streams_list = []
        try:
            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            results = await self.extract_vixcloud_url(url, client)
            for res in results:
                streams_list.append({
                    "name": f"SC 🛸 {res['quality']}", 
                    "title": "StreamingCommunity",
                    "url": res['url'],
                    "behaviorHints": {
                        "proxyHeaders": {"request": {"user-agent": User_Agent}},
                        "notWebReady": True,
                        "bingeGroup": "streamingcommunity"
                    }
                })
        except Exception:
            pass
        return streams_list

# ============================================================================
# EXTRACTOR 2: GUARDASERIE (Mixdrop/Supervideo, Voe, HDPlayer)
# ============================================================================
class GuardaserieExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['Guardaserie']['url']
        self.random_headers = Headers()

    def voe_decode(self, ct: str, luts: str) -> Dict[str, Any]:
        try:
            lut = [''.join([('\\' + x) if x in '.*+?^${}()|[]\\' else x for x in i]) for i in luts[2:-2].split("','")]
            txt = ''
            for i in ct:
                x = ord(i)
                if 64 < x < 91: x = (x - 52) % 26 + 65
                elif 96 < x < 123: x = (x - 84) % 26 + 97
                txt += chr(x)
            for i in lut: txt = re.sub(i, '', txt)
            ct = base64.b64decode(txt).decode('utf-8')
            txt = ''.join([chr(ord(i) - 3) for i in ct])
            txt = base64.b64decode(txt[::-1]).decode('utf-8')
            return json.loads(txt)
        except Exception:
            return {}

    async def extract_mixdrop(self, url: str, client: AsyncSession) -> Optional[str]:
        """Logica estratta da supervideo.py ed eval.py"""
        try:
            headers = self.random_headers.generate()
            headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            response = await client.get(url, headers=headers, impersonate='chrome', allow_redirects=True)
            
            # Cerca script packed
            soup = BeautifulSoup(response.text, "lxml", parse_only=SoupStrainer("script"))
            for script in soup.find_all("script"):
                if script.text and detect_packer(script.text):
                    unpacked = unpack_packer(script.text)
                    # Cerca MDCore.wurl (Mixdrop) o file: (altri)
                    if "xdrop" in url or "mixdrop" in url:
                        pattern = r'MDCore.wurl ?= ?"(.*?)"'
                    else:
                        pattern = r'file:"(.*?)"'
                    
                    match = re.search(pattern, unpacked)
                    if match:
                        clean_url = match.group(1)
                        if clean_url.startswith("//"): clean_url = "https:" + clean_url
                        return clean_url
            return None
        except Exception as e:
            logger.error(f"Mixdrop extraction error: {e}")
            return None

    async def extract_voe(self, url: str, client: AsyncSession) -> Optional[str]:
        try:
            headers = self.random_headers.generate()
            response = await client.get(url, headers=headers, impersonate='chrome', allow_redirects=True)
            
            redirect_match = re.search(r'''window\.location\.href\s*=\s*'([^']+)''', response.text)
            if redirect_match:
                return await self.extract_voe(redirect_match.group(1), client)

            code_match = re.search(r'json">\["([^"]+)"]</script>\s*<script\s*src="([^"]+)', response.text)
            if not code_match: return None

            script_url = urljoin(url, code_match.group(2))
            script_resp = await client.get(script_url, headers=headers)
            luts_match = re.search(r"(\[(?:'\W{2}'[,\]]){1,9})", script_resp.text)
            
            if luts_match:
                data = self.voe_decode(code_match.group(1), luts_match.group(1))
                return data.get('source')
            return None
        except Exception:
            return None

    async def resolve_supervideo(self, sv_url: str, client: AsyncSession) -> List[Dict]:
        streams = []
        try:
            # Segue i redirect per arrivare al dominio reale (voe, mixdrop, etc)
            headers = self.random_headers.generate()
            headers['Referer'] = self.domain
            response = await client.get(sv_url, headers=headers, allow_redirects=True, impersonate='chrome')
            final_url = response.url
            html_content = response.text

            # 1. Rilevamento VOE
            if "voe.sx" in final_url or "voe-network" in html_content:
                url_voe = await self.extract_voe(final_url, client)
                if url_voe:
                    streams.append({'name': "GS 🛸 Voe", 'title': "Guardaserie", 'url': url_voe})
            
            # 2. Rilevamento MIXDROP / SUPERVIDEO (Usa Eval)
            elif "mixdrop" in final_url or "supervideo" in final_url or detect_packer(html_content):
                url_mix = await self.extract_mixdrop(final_url, client)
                if url_mix:
                     streams.append({'name': "GS 🛸 MixDrop", 'title': "Guardaserie", 'url': url_mix})
            
            # 3. Rilevamento HDPLAYER (Semplice regex)
            elif "hdplayer" in final_url:
                match = re.search(r'sources:\s*\[\s*\{\s*file\s*:\s*"([^"]*)"', html_content)
                if match:
                    hdp_url = match.group(1) + ".m3u8"
                    streams.append({'name': "GS 🛸 HDPlayer", 'title': "Guardaserie", 'url': hdp_url})

        except Exception as e:
            logger.warning(f"Supervideo Resolve Error: {e}")
        return streams

    async def get_streams(self, imdb_id: str, tmdb_id: int, is_series: bool, season: str, episode: str, client: AsyncSession) -> List[Dict]:
        streams_list = []
        try:
            search_id = imdb_id if imdb_id and imdb_id.startswith('tt') else str(tmdb_id)
            headers = self.random_headers.generate()
            
            # 1. Cerca la serie/film
            search_url = f'{self.domain}/?story={search_id}&do=search&subaction=search'
            res_search = await client.get(search_url, headers=headers)
            
            soup = BeautifulSoup(res_search.text, 'lxml', parse_only=SoupStrainer('div', class_="mlnh-2"))
            div_mlnh2 = soup.select_one('div.mlnh-2:nth-of-type(2)')
            if not div_mlnh2: return []
            
            page_url = div_mlnh2.find('h2').find('a')['href']

            # 2. Trova l'episodio specifico (solo serie per ora come da file originale)
            target_url = None
            if is_series:
                res_page = await client.get(page_url, headers=headers)
                soup_page = BeautifulSoup(res_page.text, 'lxml')
                ep_link = soup_page.find('a', id=f"serie-{season}_{episode}")
                if ep_link: target_url = ep_link.get('data-link')
            
            # 3. Risolvi il player
            if target_url:
                if target_url.startswith('//'): target_url = "https:" + target_url
                extracted = await self.resolve_supervideo(target_url, client)
                streams_list.extend(extracted)

        except Exception as e:
            logger.error(f"GS Search Error: {e}")
        return streams_list

# ============================================================================
# API SETUP
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

sc_extractor = StreamingCommunityExtractor()
gs_extractor = GuardaserieExtractor()

def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

@app.get("/")
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return respond_with({"status": "online", "addon": ADDON_NAME, "manifest": f"{base_url}/U0MQ/manifest.json"})

@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "StreamingCommunity & Guardaserie (Voe/Mixdrop)",
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
        if type not in ["movie", "series"]: raise HTTPException(status_code=404)
        
        clean_imdb_id = clean_id(id)
        is_series = False
        season = episode = None
        
        if ':' in id:
            parts = id.split(':')
            clean_imdb_id = parts[0]
            if len(parts) >= 3:
                season, episode = parts[1], parts[2]
                is_series = True

        async with AsyncSession() as client:
            tmdb_id = None
            if clean_imdb_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(clean_imdb_id, client)
            else:
                try: tmdb_id = int(clean_imdb_id)
                except: pass

            if not tmdb_id: return respond_with({"streams": []})

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)
            tasks = []
            
            if CONFIG['Siti']['StreamingCommunity']['enabled']:
                tasks.append(sc_extractor.get_streams(tmdb_id, is_series, season, episode, client))
            
            if CONFIG['Siti']['Guardaserie']['enabled'] and clean_imdb_id.startswith('tt'):
                 tasks.append(gs_extractor.get_streams(clean_imdb_id, tmdb_id, is_series, season, episode, client))

            results = await asyncio.gather(*tasks)
            final_streams = []
            for res_list in results:
                for stream in res_list:
                    stream['title'] = media_title
                    final_streams.append(stream)

            return respond_with({"streams": final_streams})

    except Exception as e:
        logger.error(f"Handler Error: {e}")
        return respond_with({"streams": []})

@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})

@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
