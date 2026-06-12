"""
vidxgo.py — Estrazione M3U8 da VidXgo.

Portato da streamvix/src/extractors/vidxgo.ts (che a sua volta è un port
di MammaMia Src/API/extractors/vidxgo.py).

Logica di decriptazione:
  1. GET embed page con UA Firefox-150 e Referer altadefinizione.you
  2. Raccoglie tutti i tag <script> mantenendo gli slot degli script esterni
  3. Prende il 6° script (index 5)
  4. Regex: var <name>='KEY', d=atob('B64')
  5. base64-decode B64, XOR byte a byte con KEY (ciclico)
  6. Nel JS decriptato cerca currentSrc+"(https:[^;"]+)" → URL HLS
"""

import base64
import re
from typing import Optional

import httpx

VIDXGO_DEFAULT_DOMAIN = "https://v.vidxgo.co"

_GET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "DNT": "1",
    "Referer": "https://altadefinizione.you/",
    "Priority": "u=0, i",
}

_PLAYBACK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def decode_vidxgo_html(html: str) -> Optional[str]:
    """
    Funzione pura (nessun I/O): dato l'HTML dell'embed page
    restituisce l'URL HLS o None se il parsing fallisce.
    """
    # Raccoglie tutti i tag <script> preservando gli slot degli script esterni
    script_re = re.compile(r'<script\b([^>]*)>([\s\S]*?)<\/script>', re.IGNORECASE)
    bodies = []
    for attrs, body in script_re.findall(html):
        if re.search(r'\bsrc\s*=', attrs, re.IGNORECASE):
            bodies.append('')  # script esterno: slot preservato, corpo vuoto
        else:
            bodies.append(body)

    if len(bodies) <= 5:
        return None

    target = bodies[5]
    if not target:
        return None

    m = re.search(
        r"var\s+\w+\s*=\s*'([^']*)'\s*,\s*d\s*=\s*atob\(\s*'([^']*)'\s*\)",
        target,
    )
    if not m:
        return None

    key = m.group(1)
    b64 = m.group(2)
    if not key or not b64:
        return None

    try:
        decoded = base64.b64decode(b64)
    except Exception:
        return None

    out = bytes(decoded[i] ^ ord(key[i % len(key)]) for i in range(len(decoded)))

    url_m = re.search(r'currentSrc.+?"(https:[^";\/\\][^";\/\\][^";\/\\][^"]+)"', out.decode('utf-8', errors='ignore'))
    if not url_m:
        # pattern più permissivo
        url_m = re.search(r'currentSrc[^"]*"(https:[^"]+)"', out.decode('utf-8', errors='ignore'))
    if not url_m:
        return None

    return url_m.group(1).replace('\\', '')


def build_vidxgo_playback_headers(domain: str = VIDXGO_DEFAULT_DOMAIN) -> dict:
    """Restituisce gli header necessari per la riproduzione HLS di VidXgo."""
    return {
        "User-Agent": _PLAYBACK_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": domain.rstrip('/') + '/',
        "Origin": domain.rstrip('/'),
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "DNT": "1",
    }


async def fetch_vidxgo(
    url: str,
    client: httpx.AsyncClient,
    domain: str = VIDXGO_DEFAULT_DOMAIN,
) -> Optional[dict]:
    """
    Fetcha una embed page VidXgo ed estrae il risultato.
    Restituisce {"m3u8": str, "playback_headers": dict} oppure None.
    """
    try:
        resp = await client.get(url, headers=_GET_HEADERS, follow_redirects=True)
    except httpx.RequestError as e:
        import logging
        logging.getLogger(__name__).warning(f"[VidXgo] request error: {e}")
        return None

    if not resp.is_success:
        import logging
        logging.getLogger(__name__).warning(f"[VidXgo] HTTP {resp.status_code} per {url[:80]}")
        return None

    m3u8 = decode_vidxgo_html(resp.text)
    if not m3u8:
        import logging
        logging.getLogger(__name__).warning(f"[VidXgo] decriptazione fallita per {url[:80]}")
        return None

    return {
        "m3u8": m3u8,
        "playback_headers": build_vidxgo_playback_headers(domain),
    }
