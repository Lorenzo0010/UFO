import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Cache in memoria: (channels_list, timestamp)
_cache: dict[str, tuple[list, float]] = {}
CACHE_TTL = 3600  # 1 ora


async def fetch_m3u(url: str, session: aiohttp.ClientSession) -> str:
    """Scarica il contenuto grezzo di una playlist M3U/M3U8."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"⚠️  Impossibile scaricare playlist {url}: {e}")
        return ""


def parse_m3u(content: str, source_label: str = "") -> list[dict]:
    """
    Parsa una playlist M3U/M3U8 estesa.
    Restituisce una lista di dizionari con:
        id, name, logo, group, stream_url, source
    """
    channels = []
    lines = content.splitlines()
    current_meta: dict = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            current_meta = {}
            # Estrai attributi tvg-id, tvg-name, tvg-logo, group-title
            current_meta["tvg_id"] = _attr(line, "tvg-id")
            current_meta["tvg_name"] = _attr(line, "tvg-name")
            current_meta["logo"] = _attr(line, "tvg-logo") or _attr(line, "logo")
            current_meta["group"] = _attr(line, "group-title") or "Generale"
            # Nome canale: parte dopo l'ultima virgola
            comma_idx = line.rfind(",")
            if comma_idx != -1:
                current_meta["name"] = line[comma_idx + 1:].strip()
            else:
                current_meta["name"] = current_meta.get("tvg_name") or "Canale sconosciuto"
            current_meta["source"] = source_label

        elif line.startswith("#"):
            # Commento o tag non utile, ignora
            continue

        elif current_meta:
            # Questa è l'URL dello stream
            stream_url = line
            name = current_meta.get("name") or current_meta.get("tvg_name") or "Canale"
            tvg_id = current_meta.get("tvg_id") or ""
            # Costruisci ID univoco: usiamo tvg-id se disponibile, altrimenti slug del nome
            ch_id = tvg_id if tvg_id else _slugify(name)
            channels.append({
                "id": f"iptv:{ch_id}",
                "name": name,
                "logo": current_meta.get("logo") or "",
                "group": current_meta.get("group") or "Generale",
                "stream_url": stream_url,
                "source": source_label,
            })
            current_meta = {}

    return channels


def _attr(line: str, attr_name: str) -> str:
    """Estrae il valore di un attributo M3U dalla riga #EXTINF."""
    pattern = rf'{re.escape(attr_name)}=["\']?([^"\' ]+)["\']?'
    m = re.search(pattern, line, re.IGNORECASE)
    return m.group(1) if m else ""


def _slugify(name: str) -> str:
    """Crea uno slug sicuro da un nome canale."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s-]+", "-", slug).strip("-")


async def get_all_channels(iptv_urls: list[str]) -> list[dict]:
    """
    Scarica e parsa tutte le playlist IPTV configurate.
    Usa cache per evitare download ripetuti entro TTL.
    Deduplicazione per ID (priorità alla prima sorgente).
    """
    cache_key = "|".join(sorted(iptv_urls))
    if cache_key in _cache:
        cached_channels, cached_time = _cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            logger.info(f"✅ Canali IPTV serviti dalla cache ({len(cached_channels)} canali)")
            return cached_channels

    all_channels: list[dict] = []
    seen_ids: set[str] = set()

    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0 (compatible; UFO-Addon/1.0)"}
    ) as session:
        tasks = [
            fetch_m3u(url, session)
            for url in iptv_urls
        ]
        results = await asyncio.gather(*tasks)

    for url, content in zip(iptv_urls, results):
        if not content:
            continue
        label = url.split("/")[-1]  # es. iptvit.m3u, playlist_italy.m3u8
        channels = parse_m3u(content, source_label=label)
        for ch in channels:
            if ch["id"] not in seen_ids:
                seen_ids.add(ch["id"])
                all_channels.append(ch)

    logger.info(f"📺 Totale canali IPTV caricati: {len(all_channels)}")
    _cache[cache_key] = (all_channels, time.time())
    return all_channels


async def get_channels_by_group(
    iptv_urls: list[str],
    group: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> list[dict]:
    """Restituisce canali filtrati per gruppo con paginazione."""
    channels = await get_all_channels(iptv_urls)
    if group and group.lower() != "tutti":
        channels = [c for c in channels if c["group"].lower() == group.lower()]
    return channels[skip: skip + limit]


async def get_channel_by_id(iptv_urls: list[str], channel_id: str) -> Optional[dict]:
    """Trova un canale per il suo ID."""
    channels = await get_all_channels(iptv_urls)
    for ch in channels:
        if ch["id"] == channel_id:
            return ch
    return None


async def get_groups(iptv_urls: list[str]) -> list[str]:
    """Restituisce la lista dei gruppi/categorie unici."""
    channels = await get_all_channels(iptv_urls)
    seen: dict[str, bool] = {}
    groups = []
    for ch in channels:
        g = ch["group"]
        if g not in seen:
            seen[g] = True
            groups.append(g)
    return groups
