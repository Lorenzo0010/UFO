import base64
import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .config import ADDON_NAME, ADDON_LOGO
from .resolver import get_streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # NON combinare credentials=True con origins="*"
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def decode_config(token: str) -> dict:
    """Decodifica il token base64url in dizionario {u, p}."""
    # Ripristina padding
    rem = len(token) % 4
    if rem:
        token += "=" * (4 - rem)
    try:
        decoded = base64.urlsafe_b64decode(token).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        raise HTTPException(status_code=400, detail="Token di configurazione non valido")


CONFIG_PAGE = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name} - Configurazione</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f13;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}}
    .card{{background:#1a1a24;border:1px solid #2a2a3a;border-radius:12px;padding:2rem;width:100%;max-width:480px}}
    .logo{{width:64px;height:64px;border-radius:12px;margin:0 auto 1rem;display:block}}
    h1{{text-align:center;font-size:1.4rem;margin-bottom:.25rem;color:#fff}}
    .sub{{text-align:center;color:#888;font-size:.85rem;margin-bottom:1.75rem}}
    label{{display:block;font-size:.8rem;color:#aaa;margin-bottom:.35rem;text-transform:uppercase;letter-spacing:.05em}}
    input{{width:100%;background:#0f0f13;border:1px solid #2a2a3a;border-radius:8px;padding:.65rem .85rem;color:#e0e0e0;font-size:.95rem;margin-bottom:1rem;outline:none;transition:border-color .2s}}
    input:focus{{border-color:#7c6af7}}
    button{{width:100%;background:#7c6af7;color:#fff;border:none;border-radius:8px;padding:.75rem;font-size:1rem;font-weight:600;cursor:pointer;transition:background .2s}}
    button:hover{{background:#6a59e0}}
    .result{{display:none;margin-top:1.25rem;background:#0f0f13;border:1px solid #2a2a3a;border-radius:8px;padding:1rem}}
    .result p{{font-size:.8rem;color:#888;margin-bottom:.5rem}}
    .link-row{{display:flex;gap:.5rem;align-items:center}}
    .link-box{{flex:1;background:#1a1a24;border:1px solid #2a2a3a;border-radius:6px;padding:.5rem .75rem;font-size:.75rem;color:#c0b8ff;word-break:break-all;cursor:pointer;user-select:all}}
    .copy-btn{{width:auto;padding:.5rem .85rem;font-size:.8rem;border-radius:6px;background:#2a2a3a;flex-shrink:0}}
    .copy-btn:hover{{background:#3a3a4a}}
    .install-btn{{display:block;margin-top:.75rem;text-align:center;background:#1db954;color:#fff;text-decoration:none;border-radius:8px;padding:.65rem;font-size:.9rem;font-weight:600}}
    .install-btn:hover{{background:#17a349}}
    .note{{margin-top:.75rem;font-size:.75rem;color:#666;text-align:center}}
  </style>
</head>
<body>
  <div class="card">
    <img class="logo" src="{logo}" alt="UFO">
    <h1>{name}</h1>
    <p class="sub">Inserisci il tuo proxy MediaFlow per generare il manifest Stremio</p>
    <label for="pu">URL proxy (MediaFlow / EasyProxy)</label>
    <input id="pu" type="url" placeholder="https://mio-proxy.vercel.app" />
    <label for="pp">Password proxy (opzionale)</label>
    <input id="pp" type="password" placeholder="lascia vuoto se non richiesta" />
    <button onclick="gen()">Genera manifest</button>
    <div class="result" id="res">
      <p>Link manifest da installare in Stremio:</p>
      <div class="link-row">
        <div class="link-box" id="mlink"></div>
        <button class="copy-btn" onclick="cp()">Copia</button>
      </div>
      <a class="install-btn" id="ilink" href="#">&#9654; Installa in Stremio</a>
      <p class="note">Oppure incolla il link in Stremio → Addon → Installa da URL</p>
    </div>
  </div>
  <script>
    const BASE="{base}";
    function b64u(s){{
      // Encode UTF-8 string → base64url senza padding
      const b=btoa(encodeURIComponent(s).replace(/%([0-9A-F]{{2}})/g,(_,p)=>String.fromCharCode('0x'+p)));
      return b.replace(/\\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    }}
    function gen(){{
      const u=document.getElementById('pu').value.trim().replace(/\/+$/,'');
      const p=document.getElementById('pp').value.trim();
      if(!u){{alert('Inserisci l\'URL del proxy');return;}}
      const tok=b64u(JSON.stringify({{u,p}}));
      const mf=BASE+'/'+tok+'/manifest.json';
      document.getElementById('mlink').textContent=mf;
      document.getElementById('ilink').href=mf.replace(/^https?/,'stremio');
      document.getElementById('res').style.display='block';
    }}
    function cp(){{
      navigator.clipboard.writeText(document.getElementById('mlink').textContent)
        .then(()=>{{const b=document.querySelector('.copy-btn');b.textContent='Copiato!';setTimeout(()=>b.textContent='Copia',1500)}});
    }}
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return HTMLResponse(content=CONFIG_PAGE.format(
        name=ADDON_NAME,
        logo=ADDON_LOGO,
        base=base,
    ))


@app.get("/{token}/manifest.json")
async def manifest(token: str):
    cfg = decode_config(token)
    proxy_url = cfg.get("u", "")
    return respond_with({
        "id": f"org.stremio.ufo.{token[:12]}",
        "version": "2.0.0",
        "name": ADDON_NAME,
        "description": f"VixSrc via {proxy_url or 'proxy'}",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False,
        },
    })


@app.get("/{token}/stream/{type}/{id}.json")
async def streams_route(token: str, type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    cfg = decode_config(token)
    proxy_url = cfg.get("u", "")
    proxy_psw = cfg.get("p", "")
    if not proxy_url:
        raise HTTPException(status_code=400, detail="Proxy URL mancante")
    try:
        data = await get_streams(id, type, proxy_url, proxy_psw)
    except Exception as e:
        logging.error(f"streams error: {e}")
        data = {"streams": []}
    return respond_with(data)


@app.get("/{token}/meta/{type}/{id}.json")
async def meta(token: str, type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/{token}/catalog/{type}/{id}.json")
async def catalog(token: str, type: str, id: str):
    return respond_with({"metas": []})
