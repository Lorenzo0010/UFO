"""
guardahd.py — Provider GuardaHD / MostraGuarda per UFO.

Flusso:
  1. GET {GHD_DOMAIN}/movie/{imdb_id}  (film)
     GET {GHD_DOMAIN}/set-movie-a/{imdb_id}  (fallback)
  2. Parsing HTML: estrae embed da:
     - ul._player-mirrors > li[data-link]   (mirror principali → 1080p)
     - ._hidden-mirrors li[data-link]        (mirror alternativi → 720p)
     - Fallback: tutti i [data-link]          (se selettori nuovi vuoti)
  3. Filtra: esclude link self (mostraguarda/guardahd), streamtape
  4. Per ogni embed trovato, tenta la risoluzione con gli extractors supportati
     (Mixdrop, SuperVideo)
  5. Restituisce una lista di dict stream Stremio oppure lista vuota

Variabili d'ambiente:
  GHD_DOMAIN       default: https://mostraguarda.stream
  GUARDAHD_ENABLED default: 1  (imposta 0 per disabilitare)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from .config import GHD_DOMAIN, GUARDAHD_ENABLED
from .mixdrop import resolve_mixdrop, is_mixdrop_url
from .supervideo import resolve_supervideo, is_supervideo_url

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0)

_SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pattern per escludere link a host non supportati o self-link
_SKIP_PATTERNS = re.compile(
    r"mostraguarda|guardahd|streamtape\.com",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fetch pagina MostraGuarda
# ---------------------------------------------------------------------------

async def _fetch_page(url: str) -> Optional[str]:
    """Scarica una pagina HTML da MostraGuarda con gestione Cloudflare base."""
    headers = {
        "User-Agent": _SEARCH_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
        "Referer": GHD_DOMAIN + "/",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                logger.info(f"[GuardaHD] 404 per {url}")
                return None
            if resp.status_code != 200:
                logger.warning(f"[GuardaHD] HTTP {resp.status_code} per {url}")
                return None

        html = resp.text

        # Cloudflare challenge detection
        if "cf-turnstile" in html or "Just a moment" in html or "__cf_chl_" in html:
            logger.warning(f"[GuardaHD] Cloudflare challenge per {url}")
            return None

        return html

    except Exception as e:
        logger.warning(f"[GuardaHD] errore fetch per {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Estrazione embed URLs dall'HTML
# ---------------------------------------------------------------------------

def _extract_embed_urls(html: str) -> List[str]:
    """
    Estrae gli URL embed dalla pagina MostraGuarda.
    Replica la logica di streamvix:
      1. Mirror principali: ul._player-mirrors > li[data-link]  → res=1080p
      2. Mirror alternativi: ._hidden-mirrors li[data-link]      → res=720p
      3. Fallback generico: [data-link]
    """
    soup = BeautifulSoup(html, "lxml")
    results: List[str] = []

    def _process_tag(tag, res_hint: str = "") -> Optional[str]:
        raw = (tag.get("data-link") or "").strip()
        if not raw:
            return None

        # Normalizza URL
        if raw.startswith("//"):
            url = "https:" + raw
        elif raw.startswith("http"):
            url = raw
        else:
            return None

        # Escludi link indesiderati
        if _SKIP_PATTERNS.search(url):
            return None

        # Aggiungi hint risoluzione come fragment
        if res_hint:
            return f"{url}#res={res_hint}"
        return url

    # 1. Mirror principali → 1080p
    for tag in soup.select("ul._player-mirrors > li[data-link]"):
        url = _process_tag(tag, "1080p")
        if url:
            results.append(url)

    # 2. Mirror alternativi → 720p
    for tag in soup.select("._hidden-mirrors li[data-link]"):
        url = _process_tag(tag, "720p")
        if url:
            results.append(url)

    # 3. Fallback generico
    if not results:
        for tag in soup.select("[data-link]"):
            url = _process_tag(tag)
            if url:
                results.append(url)

    # Dedup preservando ordine
    seen = set()
    deduped = []
    for u in results:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    return deduped[:40]


# ---------------------------------------------------------------------------
# Estrazione titolo dalla pagina
# ---------------------------------------------------------------------------

def _extract_title(html: str) -> Optional[str]:
    """Estrae il titolo dal tag <h1> o <title> della pagina."""
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
        title = re.sub(r"\s*Streaming.*$", "", title, flags=re.IGNORECASE).strip()
        if title:
            return title

    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        title = re.sub(r"\s*Streaming.*$", "", title, flags=re.IGNORECASE).strip()
        if title:
            return title

    return None


# ---------------------------------------------------------------------------
# Risoluzione embed → stream Stremio
# ---------------------------------------------------------------------------

async def _resolve_single_embed(
    embed_url_raw: str,
    content_label: str,
    addon_base_url: str,
) -> Optional[dict]:
    """
    Risolve un singolo embed URL in uno stream Stremio.
    Supporta: Mixdrop, SuperVideo.
    """
    # Estrai resolution hint dal fragment
    res_hint = ""
    embed_url = embed_url_raw
    if "#res=" in embed_url_raw:
        idx = embed_url_raw.index("#res=")
        res_hint = embed_url_raw[idx + 5:]
        embed_url = embed_url_raw[:idx]

    # Tenta Mixdrop
    if is_mixdrop_url(embed_url):
        logger.info(f"[GuardaHD] 🔗 Mixdrop embed: {embed_url[:80]}")
        stream = await resolve_mixdrop(
            embed_url=embed_url,
            content_label=content_label,
            source_name="GuardaHD",
            addon_base_url=addon_base_url,
        )
        if stream and res_hint:
            stream["title"] = f"{stream.get('title', content_label)}\n📺 {res_hint}"
        return stream

    # Tenta SuperVideo
    if is_supervideo_url(embed_url):
        logger.info(f"[GuardaHD] 🔗 SuperVideo embed: {embed_url[:80]}")
        return await resolve_supervideo(
            embed_url=embed_url,
            content_label=content_label,
            source_name="GuardaHD",
            addon_base_url=addon_base_url,
            resolution_hint=res_hint,
        )

    logger.debug(f"[GuardaHD] ⏭️  host non supportato: {embed_url[:60]}")
    return None


# ---------------------------------------------------------------------------
# Funzione pubblica: resolve_guardahd
# ---------------------------------------------------------------------------

async def resolve_guardahd(
    imdb_id: str,
    content_label: str,
    content_type: str,
    addon_base_url: str,
    season: Optional[str] = None,
    episode: Optional[str] = None,
) -> List[dict]:
    """
    Resolver GuardaHD/MostraGuarda → multi-embed → stream Stremio.

    Args:
        imdb_id:        IMDB ID (es. "tt1234567")
        content_label:  Titolo del film/episodio
        content_type:   "movie" | "series"
        addon_base_url: Base URL del proxy UFO
        season:         Numero stagione (solo per serie TV)
        episode:        Numero episodio (solo per serie TV)

    Returns:
        Lista di dict stream Stremio (può essere vuota).
    """
    enabled = GUARDAHD_ENABLED not in ("0", "false", "off", "no")
    if not enabled:
        logger.debug("[GuardaHD] disabilitato via GUARDAHD_ENABLED")
        return []

    if not imdb_id or not imdb_id.startswith("tt"):
        logger.debug(f"[GuardaHD] skip — IMDB ID non valido: {imdb_id}")
        return []

    try:
        # Costruisci URL: sia film che serie usano /movie/{imdb_id} su MostraGuarda
        url = f"{GHD_DOMAIN}/movie/{imdb_id}"
        if content_type == "series" and season and episode:
            logger.info(f"[GuardaHD] 🔍 ricerca serie: {url} (S{season}E{episode})")
        else:
            logger.info(f"[GuardaHD] 🔍 ricerca film: {url}")

        html = await _fetch_page(url)
        if not html:
            # Fallback al vecchio endpoint
            fallback_url = f"{GHD_DOMAIN}/set-movie-a/{imdb_id}"
            logger.info(f"[GuardaHD] ℹ️  provo endpoint fallback: {fallback_url}")
            html = await _fetch_page(fallback_url)

        if not html:
            logger.info(f"[GuardaHD] ℹ️  nessuna pagina trovata per {imdb_id}")
            return []

        # Estrai titolo reale dalla pagina
        real_title = _extract_title(html)
        # MostraGuarda spesso restituisce titoli placeholder come "Movie tt0816692"
        # In quel caso usiamo il titolo TMDB passato dal resolver
        if real_title and imdb_id not in real_title and not re.match(r"^Movie\s+tt\d+", real_title):
            logger.info(f"[GuardaHD] 📝 titolo: {real_title}")
            label = real_title
        else:
            if real_title:
                logger.debug(f"[GuardaHD] titolo placeholder ignorato: {real_title}")
            label = content_label

        # Estrai tutti gli embed URL
        embed_urls = _extract_embed_urls(html)
        logger.info(f"[GuardaHD] 🔗 embed trovati: {len(embed_urls)}")

        if not embed_urls:
            logger.info(f"[GuardaHD] ℹ️  nessun embed per {imdb_id}")
            return []

        # Risolvi tutti gli embed in parallelo
        tasks = [
            _resolve_single_embed(eu, label, addon_base_url)
            for eu in embed_urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        streams: List[dict] = []
        seen_urls: set = set()
        for r in results:
            if isinstance(r, Exception):
                logger.debug(f"[GuardaHD] embed exception: {r}")
                continue
            if isinstance(r, dict) and r.get("url"):
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    streams.append(r)

        logger.info(f"[GuardaHD] ✅ stream totali: {len(streams)}")
        return streams

    except Exception as e:
        logger.warning(f"[GuardaHD] ❌ errore inatteso per {imdb_id}: {e}")
        return []
