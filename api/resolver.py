import asyncio
import logging
from typing import Dict, Optional
from urllib.parse import quote

from playwright.async_api import async_playwright

from .config import SC_DOMAIN, EASYPROXY_URL, EASYPROXY_PSW, USER_AGENT
from .tmdb import get_tmdb_info, get_episode_title

logger = logging.getLogger(__name__)

_INTERCEPT_TIMEOUT = 20


def build_easyproxy_url(m3u8_url: str) -> str:
    encoded = quote(m3u8_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url


async def extract_m3u8(page_url: str) -> Optional[str]:
    found: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            java_script_enabled=True,
        )
        page = await context.new_page()

        async def on_request(request):
            url = request.url
            # Log ogni richiesta per diagnostica
            logger.debug(f"[REQ] {request.method} {url[:120]}")
            if ".m3u8" in url and not found:
                logger.info(f"🔍 M3U8 intercettato: {url[:120]}")
                found.append(url)

        async def on_response(response):
            url = response.url
            status = response.status
            # Log solo risposte non-OK o tipi media interessanti
            ct = response.headers.get("content-type", "")
            if status >= 300 or "video" in ct or "mpegurl" in ct or ".m3u8" in url:
                logger.info(f"[RES] {status} {url[:120]} | {ct}")

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            logger.info(f"🌐 Playwright: navigazione verso {page_url}")
            resp = await page.goto(page_url, wait_until="domcontentloaded", timeout=15_000)
            logger.info(f"🌐 Playwright: pagina caricata, status={resp.status if resp else 'N/A'}")

            # Aspetta che il player JS faccia partire la richiesta M3U8
            deadline = asyncio.get_event_loop().time() + _INTERCEPT_TIMEOUT
            while not found and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.3)

            # Se non trovato, dumpa il titolo della pagina per capire cosa ha caricato
            if not found:
                title = await page.title()
                logger.warning(f"⚠️ Timeout senza M3U8. Titolo pagina: '{title}'")
                # Dumpa i primi 500 char del body per vedere se c'e' un redirect/captcha
                try:
                    body_text = await page.evaluate("() => document.body?.innerText?.slice(0, 500)")
                    logger.warning(f"⚠️ Body snippet: {body_text!r}")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"❌ Playwright errore: {e}")
        finally:
            await browser.close()

    if found:
        return found[0]

    logger.warning(f"❌ Nessun M3U8 trovato per {page_url}")
    return None


async def get_streams(stremio_id: str, content_type: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts = stremio_id.split(":")
        content_id = parts[0]
        season = parts[1] if len(parts) > 1 else None
        episode = parts[2] if len(parts) > 2 else None
        is_series = content_type == "series" and season and episode

        if is_series:
            tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
                return result

            page_url = f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
            logger.info(f"🎬 VixSrc page: {page_url}")

            if not EASYPROXY_URL:
                logger.error("❌ EASYPROXY_URL non configurato")
                return result

            ep_title_task = asyncio.create_task(get_episode_title(tmdb_id, season, episode))
            real_m3u8 = await extract_m3u8(page_url)
            ep_title = await ep_title_task
            content_label = ep_title or tmdb_title or ""
        else:
            tmdb_id, tmdb_title = await get_tmdb_info(content_id, content_type)
            if not tmdb_id:
                logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
                return result

            page_url = f"{SC_DOMAIN}/movie/{tmdb_id}/"
            logger.info(f"🎬 VixSrc page: {page_url}")

            if not EASYPROXY_URL:
                logger.error("❌ EASYPROXY_URL non configurato")
                return result

            real_m3u8 = await extract_m3u8(page_url)
            content_label = tmdb_title or "Film"

        if not real_m3u8:
            logger.error(f"❌ Impossibile estrarre M3U8 per {page_url}")
            return result

        stream_url = build_easyproxy_url(real_m3u8)
        logger.info(f"✅ EasyProxy stream: {stream_url[:80]}...")

        result["streams"].append({
            "name": "UFO\n🇮🇹",
            "title": content_label,
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": True,
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result
