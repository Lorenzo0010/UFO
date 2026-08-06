import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse

from .config import ADDON_NAME, ADDON_LOGO, ADDON_BASE_URL, validate_config
from .proxy import router as proxy_router, close_proxy_client
from .resolver import get_streams, PROVIDER_STATS
from .tmdb import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    yield
    await close_session()
    await close_proxy_client()
    logger.info("🔌 Sessioni HTTP chiuse")


app = FastAPI(title=f"{ADDON_NAME} Addon", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registra il proxy HLS interno
app.include_router(proxy_router)


def _get_base(request: Request) -> str:
    """Restituisce il base URL del proxy.
    Priorità: ADDON_BASE_URL (env, fisso) > request.base_url (dinamico).
    ADDON_BASE_URL è necessario quando più client con IP diversi accedono
    all'addon (es. Stremio desktop su più client).
    """
    if ADDON_BASE_URL:
        return ADDON_BASE_URL
    return str(request.base_url).rstrip("/")


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/")
async def root(request: Request):
    base = _get_base(request)
    manifest_url = f"{base}/manifest.json"
    
    def calc_stats(provider: str):
        stats = PROVIDER_STATS[provider]
        if stats["total"] == 0:
            return 0
        return int((stats["success"] / stats["total"]) * 100)

    vix_perc = calc_stats("vixcloud")
    vidx_perc = calc_stats("vidxgo")

    vix_color = "green" if vix_perc >= 50 or PROVIDER_STATS["vixcloud"]["total"] == 0 else "red"
    vidx_color = "green" if vidx_perc >= 50 or PROVIDER_STATS["vidxgo"]["total"] == 0 else "red"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{ADDON_NAME} - Status</title>
        <style>
            :root {{
                --bg-color: #0f172a;
                --text-color: #f8fafc;
                --card-bg: rgba(30, 41, 59, 0.7);
                --accent-color: #3b82f6;
                --accent-hover: #2563eb;
            }}
            body {{
                margin: 0;
                padding: 0;
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                color: var(--text-color);
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }}
            .container {{
                background: var(--card-bg);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 1rem;
                padding: 2.5rem;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                text-align: center;
                max-width: 450px;
                width: 90%;
            }}
            .logo {{
                width: 120px;
                height: 120px;
                object-fit: contain;
                margin-bottom: 1rem;
                border-radius: 20%;
                filter: drop-shadow(0 4px 6px rgba(0,0,0,0.3));
            }}
            h1 {{
                margin: 0 0 0.5rem 0;
                font-size: 1.8rem;
                font-weight: 700;
            }}
            .status-badge {{
                display: inline-block;
                background: rgba(16, 185, 129, 0.1);
                color: #10b981;
                padding: 0.25rem 0.75rem;
                border-radius: 9999px;
                font-size: 0.875rem;
                font-weight: 500;
                margin-bottom: 2rem;
                border: 1px solid rgba(16, 185, 129, 0.2);
            }}
            .link-box {{
                background: rgba(15, 23, 42, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 1rem;
                border-radius: 0.5rem;
                margin-bottom: 2rem;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 1rem;
            }}
            .link-text {{
                font-family: monospace;
                font-size: 0.9rem;
                color: #cbd5e1;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .copy-btn {{
                background: var(--accent-color);
                color: white;
                border: none;
                padding: 0.5rem 1rem;
                border-radius: 0.25rem;
                cursor: pointer;
                font-weight: 500;
                transition: background 0.2s;
            }}
            .copy-btn:hover {{
                background: var(--accent-hover);
            }}
            .stats-container {{
                display: flex;
                flex-direction: column;
                gap: 1rem;
                text-align: left;
            }}
            .stat-card {{
                background: rgba(15, 23, 42, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 1rem;
                border-radius: 0.5rem;
                display: flex;
                align-items: center;
                justify-content: space-between;
            }}
            .stat-info {{
                display: flex;
                flex-direction: column;
            }}
            .stat-name {{
                font-weight: 600;
                font-size: 1rem;
            }}
            .stat-sub {{
                font-size: 0.8rem;
                color: #94a3b8;
            }}
            .led-container {{
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }}
            .led {{
                width: 12px;
                height: 12px;
                border-radius: 50%;
                box-shadow: 0 0 10px currentColor;
            }}
            .led.green {{ background-color: #10b981; color: #10b981; }}
            .led.red {{ background-color: #ef4444; color: #ef4444; }}
            .perc {{
                font-weight: bold;
                font-size: 1.1rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <img src="{ADDON_LOGO}" alt="Logo" class="logo">
            <h1>{ADDON_NAME}</h1>
            <div class="status-badge">● Online</div>
            
            <div class="link-box">
                <div class="link-text" id="manifest-link">{manifest_url}</div>
                <button class="copy-btn" onclick="copyLink()">Copy</button>
            </div>

            <div class="stats-container">
                <div class="stat-card">
                    <div class="stat-info">
                        <span class="stat-name">VixSrc</span>
                        <span class="stat-sub">{{PROVIDER_STATS['vixcloud']['total']}} requests</span>
                    </div>
                    <div class="led-container">
                        <span class="perc">{vix_perc}%</span>
                        <div class="led {vix_color}"></div>
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-info">
                        <span class="stat-name">VidXgo</span>
                        <span class="stat-sub">{{PROVIDER_STATS['vidxgo']['total']}} requests</span>
                    </div>
                    <div class="led-container">
                        <span class="perc">{vidx_perc}%</span>
                        <div class="led {vidx_color}"></div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            function copyLink() {{
                const linkText = document.getElementById('manifest-link').innerText;
                navigator.clipboard.writeText(linkText).then(() => {{
                    const btn = document.querySelector('.copy-btn');
                    btn.innerText = 'Copied!';
                    setTimeout(() => {{ btn.innerText = 'Copy'; }}, 2000);
                }}).catch(err => {{
                    console.error('Failed to copy: ', err);
                }});
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.6.0",
        "name": ADDON_NAME,
        "description": "VixSrc con proxy HLS interno",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False},
    })


@app.get("/stream/{type}/{id}.json")
async def streams_route(type: str, id: str, request: Request):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    try:
        base = _get_base(request)
        data = await get_streams(id, type, addon_base_url=base)
    except Exception:
        logger.exception(f"❌ Errore non gestito in streams_route per id={id!r} type={type!r}")
        data = {"streams": []}
    return respond_with(data)


@app.get("/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
