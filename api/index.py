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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def encode_config(proxy_url: str, proxy_password: str) -> str:
    cfg = json.dumps({"u": proxy_url.rstrip("/"), "p": proxy_password})
    return base64.urlsafe_b64encode(cfg.encode()).decode().rstrip("=")


def decode_config(token: str) -> dict:
    pad = 4 - len(token) % 4
    if pad != 4:
        token += "=" * pad
    try:
        return json.loads(base64.urlsafe_b64decode(token).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Configurazione non valida")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{ADDON_NAME} - Configurazione</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f0f13;
      color: #e0e0e0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }}
    .card {{
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 12px;
      padding: 2rem;
      width: 100%;
      max-width: 480px;
    }}
    .logo {{
      width: 64px;
      height: 64px;
      border-radius: 12px;
      margin: 0 auto 1rem;
      display: block;
    }}
    h1 {{
      text-align: center;
      font-size: 1.4rem;
      margin-bottom: 0.25rem;
      color: #fff;
    }}
    .subtitle {{
      text-align: center;
      color: #888;
      font-size: 0.85rem;
      margin-bottom: 1.75rem;
    }}
    label {{
      display: block;
      font-size: 0.8rem;
      color: #aaa;
      margin-bottom: 0.35rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    input {{
      width: 100%;
      background: #0f0f13;
      border: 1px solid #2a2a3a;
      border-radius: 8px;
      padding: 0.65rem 0.85rem;
      color: #e0e0e0;
      font-size: 0.95rem;
      margin-bottom: 1rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    input:focus {{ border-color: #7c6af7; }}
    button {{
      width: 100%;
      background: #7c6af7;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 0.75rem;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }}
    button:hover {{ background: #6a59e0; }}
    .result {{
      display: none;
      margin-top: 1.25rem;
      background: #0f0f13;
      border: 1px solid #2a2a3a;
      border-radius: 8px;
      padding: 1rem;
    }}
    .result p {{ font-size: 0.8rem; color: #888; margin-bottom: 0.5rem; }}
    .link-row {{
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }}
    .link-box {{
      flex: 1;
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 6px;
      padding: 0.5rem 0.75rem;
      font-size: 0.78rem;
      color: #c0b8ff;
      word-break: break-all;
      cursor: pointer;
      user-select: all;
    }}
    .copy-btn {{
      width: auto;
      padding: 0.5rem 0.85rem;
      font-size: 0.8rem;
      border-radius: 6px;
      background: #2a2a3a;
      flex-shrink: 0;
    }}
    .copy-btn:hover {{ background: #3a3a4a; }}
    .install-btn {{
      display: block;
      margin-top: 0.75rem;
      text-align: center;
      background: #1db954;
      color: #fff;
      text-decoration: none;
      border-radius: 8px;
      padding: 0.65rem;
      font-size: 0.9rem;
      font-weight: 600;
    }}
    .install-btn:hover {{ background: #17a349; }}
  </style>
</head>
<body>
  <div class="card">
    <img class="logo" src="{ADDON_LOGO}" alt="UFO">
    <h1>{ADDON_NAME}</h1>
    <p class="subtitle">Inserisci il tuo proxy MediaFlow per generare il manifest</p>

    <label for="proxy_url">URL del proxy (MediaFlow / EasyProxy)</label>
    <input id="proxy_url" type="url" placeholder="https://mio-proxy.vercel.app" />

    <label for="proxy_psw">Password del proxy (opzionale)</label>
    <input id="proxy_psw" type="password" placeholder="lascia vuoto se non richiesta" />

    <button onclick="generate()">Genera manifest</button>

    <div class="result" id="result">
      <p>Copia questo link e installalo in Stremio:</p>
      <div class="link-row">
        <div class="link-box" id="manifest_link"></div>
        <button class="copy-btn" onclick="copyLink()">Copia</button>
      </div>
      <a class="install-btn" id="install_link" href="#" target="_blank">&#9654; Installa in Stremio</a>
    </div>
  </div>

  <script>
    const BASE = "{base}";

    function b64url(str) {{
      const bytes = new TextEncoder().encode(str);
      let bin = "";
      bytes.forEach(b => bin += String.fromCharCode(b));
      return btoa(bin).replace(/\\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }}

    function generate() {{
      const url = document.getElementById("proxy_url").value.trim().replace(/\/+$/, "");
      const psw = document.getElementById("proxy_psw").value.trim();
      if (!url) {{ alert("Inserisci l'URL del proxy"); return; }}
      const cfg = JSON.stringify({{u: url, p: psw}});
      const token = b64url(cfg);
      const manifest = BASE + "/" + token + "/manifest.json";
      const stremio  = manifest.replace(/^https?/, "stremio");
      document.getElementById("manifest_link").textContent = manifest;
      document.getElementById("install_link").href = stremio;
      document.getElementById("result").style.display = "block";
    }}

    function copyLink() {{
      const txt = document.getElementById("manifest_link").textContent;
      navigator.clipboard.writeText(txt).then(() => {{
        const btn = document.querySelector(".copy-btn");
        btn.textContent = "Copiato!";
        setTimeout(() => btn.textContent = "Copia", 1500);
      }});
    }}
  </script>
</body>
</html>
""")


@app.get("/{token}/manifest.json")
async def manifest(token: str):
    cfg = decode_config(token)
    return respond_with({
        "id": f"org.stremio.ufo.{token[:8]}",
        "version": "2.0.0",
        "name": ADDON_NAME,
        "description": f"VixSrc via proxy: {cfg.get('u', '')}",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": True, "configurationRequired": False},
    })


@app.get("/{token}/stream/{type}/{id}.json")
async def streams_route(token: str, type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    cfg = decode_config(token)
    proxy_url = cfg.get("u", "")
    proxy_psw = cfg.get("p", "")
    if not proxy_url:
        raise HTTPException(status_code=400, detail="Proxy URL mancante nella configurazione")
    try:
        data = await get_streams(id, type, proxy_url, proxy_psw)
    except Exception:
        data = {"streams": []}
    return respond_with(data)


@app.get("/{token}/meta/{type}/{id}.json")
async def meta(token: str, type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/{token}/catalog/{type}/{id}.json")
async def catalog(token: str, type: str, id: str):
    return respond_with({"metas": []})
