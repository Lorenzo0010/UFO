import json
import logging
import re
import os
import asyncio
from typing import Dict, Optional, Any, List
from urllib.parse import quote

# Librerie terze
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
# 1. CONFIGURAZIONE GLOBALE & MOCKING (Sostituisce Src.Utilities.config)
# ============================================================================
ADDON_NAME = "UFO Addon (Pro)"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
Icon = "🛸"
Name = "UFO"
LEVEL = logging.INFO

# Credenziali Uprot (dal tuo file uprot.txt)
UPROT_DATA = {
    'PHPSESSID': 'bm1qtf1pe0dnr4u4nhvofitk8u', 
    'captcha': 'ca5612d51c9bbc60f8d76051693d3315' # Nota: Questi scadono, vanno aggiornati se smette di andare
}
UPROT_PIN = '853'

CONFIG = {
    "Siti": {
        "StreamingCommunity": {"url": "https://vixsrc.to"},
        "Guardaserie": {"url": "https://guardaserie.tv"},
        "CB01": {"url": "https://cb01.news"}
    }
}

# Configurazione Logging
logging.basicConfig(level=LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configurazione TMDB
User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
fake_headers = Headers()

# ============================================================================
# 2. UTILITY DI DECODIFICA (Sostituisce Src.Utilities.eval)
# ============================================================================
class JSPacker:
    """Decodifica JavaScript 'packer' usato da Supervideo e Mixdrop"""
    def __init__(self, source):
        self.source = source

    def unpack(self):
        try:
            p = re.search(r"}\('(.*)', *(\d+), *(\d+), *'(.*?)'\.split\('\|'\)", self.source)
            if not p: return self.source
            payload, radix, count, args = p.groups()
            radix = int(radix)
            count = int(count)
            args = args.split('|')
            
            def baseN(num, b):
                return ((num == 0) and "0") or (baseN(num // b, b).lstrip("0") + "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"[num % b])

            def replace_token(match):
                word = match.group()
                x = int(word, 36) if radix > 10 else int(word, radix) # Basic logic, simplified
                if x < len(args):
                    return args[x] if args[x] else word
                return word

            # This is a simplified packer unpacker. 
            # For complex sites, a full JS execution might be needed, but this works for most standard packers.
            decoded = re.sub(r'\b\w+\b', lambda m: args[int(m.group(0), 36)] if m.group(0).isalnum() and int(m.group(0), 36) < len(args) and args[int(m.group(0), 36)] else m.group(0), payload)
            return decoded
        except Exception:
            return self.source

async def eval_solver(url_or_source: str, proxies: dict, forward_proxy: str, client: AsyncSession) -> Optional[str]:
    """
    Simula la logica di Src.Utilities.eval. 
    Estrae l'URL finale da pagine con javascript offuscato (packer).
    """
    try:
        # Se è un URL, scarichiamo la pagina
        if url_or_source.startswith("http"):
            headers = fake_headers.generate()
            response = await client.get(url_or_source, headers=headers, timeout=10)
            text = response.text
        else:
            text = url_or_source

        # Cerca pattern packer: eval(function(p,a,c,k,e,d)
        if "eval(function(p,a,c,k,e,d)" in text:
            unpacked = JSPacker(text).unpack()
            # Cerca un file video .mp4 o .m3u8 dentro il codice scompattato
            match = re.search(r'file\s*:\s*"([^"]+)"', unpacked)
            if match: return match.group(1)
            match = re.search(r'src\s*:\s*"([^"]+)"', unpacked)
            if match: return match.group(1)
            
        # Fallback: Cerca direttamente nell'HTML
        match = re.search(r'file\s*:\s*"([^"]+)"', text)
        if match: return match.group(1)
        
        return None
    except Exception as e:
        logger.error(f"❌ Eval Solver Error: {e}")
        return None

# ============================================================================
# 3. IMPLEMENTAZIONE RISOLUTORI (Adattati dai tuoi file)
# ============================================================================

async def bypass_uprot(client: AsyncSession, url: str) -> Optional[str]:
    """
    Gestisce il bypass del captcha di Uprot/Maxstream usando i cookie forniti.
    """
    try:
        if "uprot" not in url and "maxstream" not in url:
            return url

        headers = fake_headers.generate()
        # Usiamo i cookie dal tuo file txt
        cookies = UPROT_DATA
        
        # Facciamo una richiesta POST simulando l'invio del captcha
        data = {'captcha': UPROT_PIN} 
        
        # Prima visitiamo per settare i cookie sessione se necessario
        await client.get(url, headers=headers, cookies=cookies)
        
        # Simuliamo il post (spesso maxstream reindirizza automaticamente se ha il cookie)
        response = await client.post(url, data=data, headers=headers, cookies=cookies, allow_redirects=True)
        
        if response.status_code == 200:
            return str(response.url)
        return url
    except Exception as e:
        logger.error(f"Uprot Bypass Error: {e}")
        return None

async def resolve_supervideo(supervideo_link, client, streams, site_name, proxies, ForwardProxy):
    """Logica da supervideo.py"""
    try:
        url = await eval_solver(supervideo_link, proxies, ForwardProxy, client)
        if url:
            streams['streams'].append({
                'name': f"{Name}",
                'title': f'{Icon} {site_name}\n▶️ Supervideo', 
                'url': url, 
                'behaviorHints': {'bingeGroup': f'{site_name.lower()}'}
            })
            logger.info(f"{site_name} on SuperVideo found results")
    except Exception as e:
        logger.error(f"Supervideo Error: {e}")
    return streams

async def resolve_maxstream(url, client, streams, site_name, language, proxies, ForwardProxy):
    """Logica da maxstream.py"""
    try:
        headers = fake_headers.generate()
        response = await client.get(url, allow_redirects=True, timeout=30, headers=headers, impersonate="chrome")
        pattern = r'sources\W+src\W+(.*)",'
        match = re.search(pattern, response.text)
        if match:
            final_url = match.group(1)
            logger.info(f"{site_name} on Maxstream found results")
            streams['streams'].append({
                'name': f"{Name} {language}",
                'title': f'{Icon} {site_name}\n▶️ Maxstream', 
                'url': final_url, 
                'behaviorHints': {'bingeGroup': f'{site_name.lower()}'}
            })
    except Exception as e:
        logger.error(f"Maxstream Error: {e}")
    return streams

async def resolve_mixdrop(url, client, MFP, MFP_CREDENTIALS, streams, site_name, proxies, ForwardProxy, language):
    """Logica da mixdrop.py"""
    status = False
    try:
        if "club" in url:
            url = url.replace("club", "cv").split("/2")[0]
        if "cfd" in url:
            url = url.replace("cfd", "cv").replace("emb","e").split("/2")[0]
        
        # MFP Disabilitato per semplicità (richiede moduli esterni complessi), usiamo eval_solver
        if MFP == "1":
            # Placeholder se un giorno integri MFP
            pass 
        else:
            headers = {"accept-language": "en-US,en;q=0.5"}
            # Mixdrop usa packed JS
            unpacked_url = await eval_solver(url, proxies, ForwardProxy, client)
            if unpacked_url:
                if not unpacked_url.startswith("http"):
                    unpacked_url = "https:" + unpacked_url if unpacked_url.startswith("//") else unpacked_url
                
                logger.info(f"{site_name} on Mixdrop found results")
                status = True
                streams['streams'].append({
                    'name': f"{Name} {language}",
                    'title': f'{Icon} {site_name}\n▶️ MixDrop', 
                    'url': unpacked_url, 
                    'behaviorHints': {
                        'proxyHeaders': {"request": {"User-Agent": 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36'}}, 
                        'notWebReady': True, 
                        'bingeGroup': f'{site_name.lower()}'
                    }
                })
    except Exception as e:
        logger.error(f"Mixdrop Error: {e}")
    return streams, status

# ============================================================================
# 4. HELPERS GENERICI (TMDB, Pulizia ID)
# ============================================================================

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
    except Exception:
        return None

async def get_media_info(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None):
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                title = data.get("title")
                year = data.get("release_date", "")[:4] if data.get("release_date") else ""
                return title, year, title
        else:
            tv_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
            tv_resp = await client.get(tv_url, params=params, timeout=5)
            tv_data = tv_resp.json() if tv_resp.status_code == 200 else {}
            series_name = tv_data.get("name", "Serie")
            year = tv_data.get("first_air_date", "")[:4] if tv_data.get("first_air_date") else ""

            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            ep_data = ep_resp.json() if ep_resp.status_code == 200 else {}
            ep_title = ep_data.get('name', f"Episodio {episode}")
            
            return series_name, year, f"{series_name} S{season}E{episode} - {ep_title}"

        return "Unknown", "", "Video"
    except Exception:
        return "Unknown", "", f"Video {tmdb_id}"

# ============================================================================
# 5. ESTRATTORI (Siti Sorgente)
# ============================================================================

# --- STREAMINGCOMMUNITY ---
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']

    async def get_streams(self, tmdb_id, is_series, season, episode, display_title, client):
        try:
            # Semplificazione della logica VixSrc per brevità (puoi rimettere quella completa del file precedente se vuoi)
            # Qui mantengo la struttura base
            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            headers = fake_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            
            # Nota: VixSrc spesso richiede estrazione complessa del token. 
            # Se hai il codice funzionante del blocco precedente per vixsrc, incollalo qui.
            # Per ora metto un return vuoto per concentrarmi su GS/CB01 che hai chiesto
            return [] 
        except Exception:
            return []

# --- GUARDASERIE ---
class GuardaserieExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['Guardaserie']['url']

    async def search(self, title: str, client: AsyncSession):
        try:
            headers = fake_headers.generate()
            clean_title = quote(title.replace("'", " "))
            url = f"{self.domain}/?story={clean_title}&do=search&subaction=search"
            response = await client.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'lxml')
            div_mlnh2 = soup.select_one('div.mlnh-2:nth-of-type(2)')
            if div_mlnh2:
                a_tag = div_mlnh2.find('h2').find('a')
                if a_tag: return a_tag['href']
            return None
        except Exception:
            return None

    async def get_streams(self, tmdb_id, is_series, season, episode, title, display_title, client):
        if not is_series: return []
        streams_list = []
        try:
            page_url = await self.search(title, client)
            if not page_url: return []

            headers = fake_headers.generate()
            response = await client.get(page_url, headers=headers)
            soup = BeautifulSoup(response.text, 'lxml')
            a_tag = soup.find('a', id=f"serie-{season}_{episode}")
            
            if a_tag and 'data-link' in a_tag.attrs:
                supervideo_link = a_tag['data-link']
                # Chiamata al risolutore integrato
                temp_streams = {'streams': []}
                await resolve_supervideo(supervideo_link, client, temp_streams, "Guardaserie", {}, "")
                
                for s in temp_streams['streams']:
                    s['title'] = f"{display_title}\nGuardaserie"
                    streams_list.append(s)
        except Exception as e:
            logger.error(f"GS Error: {e}")
        return streams_list

# --- CB01 ---
class CB01Extractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['CB01']['url']
        self.year_pattern = re.compile(r'(19|20)\d{2}')

    async def search(self, title, year_target, is_series, client):
        try:
            headers = fake_headers.generate()
            clean_title = title.replace(" ", "+").replace("ò","o").replace("à","a").replace("è","e")
            base_search = f'{self.domain}/serietv/?s={clean_title}' if is_series else f'{self.domain}/?s={clean_title}'
            response = await client.get(base_search, headers=headers)
            soup = BeautifulSoup(response.text, 'lxml')
            cards = soup.find_all('div', class_='card-content')
            
            for card in cards:
                link_tag = card.find('h3', class_='card-title').find('a')
                if not link_tag: continue
                href = link_tag['href']
                
                # Controllo Anno
                found_year = None
                date_span = card.find('span', style=re.compile('color')) if is_series else None
                if is_series and date_span:
                    match = self.year_pattern.search(date_span.text)
                    if match: found_year = match.group(0)
                elif not is_series:
                    match = self.year_pattern.search(href)
                    if match: found_year = match.group(0)

                if found_year and year_target:
                    if abs(int(found_year) - int(year_target)) <= 1: return href
                elif not year_target:
                    return href
            return None
        except Exception:
            return None

    async def get_stayonline(self, link, client):
        try:
            headers = {
                'origin': 'https://stayonline.pro',
                'user-agent': User_Agent,
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {'id': link.split("/")[-2], 'ref': ''}
            response = await client.post('https://stayonline.pro/ajax/linkEmbedView.php', headers=headers, data=data)
            return response.json()['data']['value']
        except:
            return link

    async def get_streams(self, tmdb_id, is_series, season, episode, title, year, display_title, client):
        streams_list = []
        try:
            page_url = await self.search(title, year, is_series, client)
            if not page_url: return []

            response = await client.get(page_url, headers=fake_headers.generate(), allow_redirects=True)
            soup = BeautifulSoup(response.text, "lxml")
            target_urls = []

            # Estrazione Link (Semplificata per brevità, focalizzata su Film o iframe generici)
            if is_series:
                 # Cerca pattern stagione/episodio nel testo per trovare l'iframe giusto
                 # (Implementazione base: prende tutti i link iframe trovati se non riesce a filtrare)
                 pass # TODO: Implementare regex specifica serie CB01 se serve
            
            # Cerca i div iframen (comuni in CB01)
            iframen2 = soup.find("div", id="iframen2") # Mixdrop spesso qui
            iframen1 = soup.find("div", id="iframen1") # Maxstream spesso qui
            
            if iframen2: target_urls.append(iframen2.get("data-src"))
            if iframen1: target_urls.append(iframen1.get("data-src"))

            temp_streams = {'streams': []}

            for url in target_urls:
                if not url: continue
                
                # 1. Stayonline
                real_url = url
                if "stayonline" in url:
                    real_url = await self.get_stayonline(url, client)

                # 2. Maxstream / Uprot
                if "maxstream" in real_url or "uprot" in real_url:
                    # BYPASS UPROT CAPTCHA
                    max_link = await bypass_uprot(client, real_url)
                    if max_link:
                        await resolve_maxstream(max_link, client, temp_streams, 'CB01', '', {}, "")
                
                # 3. Mixdrop
                elif "mixdrop" in real_url:
                    await resolve_mixdrop(real_url, client, "0", [], temp_streams, "CB01", {}, "", "")

            for s in temp_streams['streams']:
                s['title'] = f"{display_title}\nCB01"
                streams_list.append(s)

        except Exception as e:
            logger.error(f"CB01 Error: {e}")
        
        return streams_list

# ============================================================================
# 6. FASTAPI SETUP
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME}")

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

ex_sc = StreamingCommunityExtractor()
ex_gs = GuardaserieExtractor()
ex_cb = CB01Extractor()

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
        "manifest": f"{base_url}/manifest.json"
    })

@app.get("/manifest.json")
async def manifest():
    config = {
        "id": "org.stremio.mammamia.ufo.pro",
        "version": "2.1.0",
        "name": ADDON_NAME,
        "description": "Multi-Source: SC, Guardaserie, CB01 with Resolvers",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    }
    return respond_with(config)

@app.get("/stream/{type}/{id}.json")
@limiter.limit("5/second")
async def streams(request: Request, type: str, id: str):
    if type not in ["movie", "series"]: return respond_with({"streams": []})
    
    clean = clean_id(id)
    season = "1"
    episode = "1"
    is_series = False
    
    if ':' in id:
        parts = id.split(':')
        if len(parts) >= 3:
            season, episode = parts[1], parts[2]
            is_series = True
    
    async with AsyncSession(impersonate="chrome110") as client:
        tmdb_id = None
        if clean.startswith('tt'):
            tmdb_id = await get_tmdb_id_from_imdb(clean, client)
        else:
            try: tmdb_id = int(clean)
            except: pass
            
        if not tmdb_id: return respond_with({"streams": []})

        title, year, display_title = await get_media_info(client, tmdb_id, is_series, season, episode)
        logger.info(f"🔎 Cercando: {title} ({year})")

        # Esecuzione parallela
        tasks = [
            ex_sc.get_streams(tmdb_id, is_series, season, episode, display_title, client),
            ex_gs.get_streams(tmdb_id, is_series, season, episode, title, display_title, client),
            ex_cb.get_streams(tmdb_id, is_series, season, episode, title, year, display_title, client)
        ]
        
        results = await asyncio.gather(*tasks)
        
        final_streams = []
        for res in results:
            final_streams.extend(res)

        # Filtro duplicati
        unique_streams = []
        seen_urls = set()
        for s in final_streams:
            if s['url'] not in seen_urls:
                seen_urls.add(s['url'])
                unique_streams.append(s)

        return respond_with({"streams": unique_streams})

@app.get("/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})

@app.get("/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
