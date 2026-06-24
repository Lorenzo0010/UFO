"""
mixdrop.py — Extractor per Mixdrop (mixdrop.cv / mixdrop.co).

Flusso:
  1. GET della pagina embed (es. https://mixdrop.cv/e/<id>)
  2. Trova il tag <script> con eval(function(p,a,c,k,e,d){...}) (p.a.c.k.e.r)
  3. Deoffusca con unpack()
  4. Regex MDCore.wurl = "<url>" sul codice deoffuscato
  5. Restituisce "https:" + url come stream diretto MP4

Non richiede dipendenze esterne oltre httpx e BeautifulSoup4.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup, SoupStrainer

from .proxy import encode_headers_b64
from .config import USER_AGENT

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(25.0)

_MIXDROP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)

_WURL_RE = re.compile(r'MDCore\.wurl\s*[=?]+\s*"(.*?)"')


# ---------------------------------------------------------------------------
# p.a.c.k.e.r unpacker (ported from js-beautify / MammaMia)
# ---------------------------------------------------------------------------

class _UnpackingError(Exception):
    pass


class _Unbaser:
    ALPHABET = {
        62: "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        95: (
            " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
        ),
    }

    def __init__(self, base: int):
        self.base = base
        if 36 < base < 62:
            if base not in self.ALPHABET:
                self.ALPHABET[base] = self.ALPHABET[62][:base]
        if 2 <= base <= 36:
            self.unbase = lambda s: int(s, base)
        else:
            try:
                self.dictionary = {
                    cipher: index
                    for index, cipher in enumerate(self.ALPHABET[base])
                }
            except KeyError:
                raise TypeError("Unsupported base encoding.")
            self.unbase = self._dictunbaser

    def __call__(self, string: str) -> int:
        return self.unbase(string)

    def _dictunbaser(self, string: str) -> int:
        ret = 0
        for index, cipher in enumerate(reversed(string)):
            ret += (self.base ** index) * self.dictionary[cipher]
        return ret


def _packer_detect(source: str) -> bool:
    return "eval(function(p,a,c,k,e,d)" in source


def _packer_filterargs(source: str):
    juicers = [
        r"}\('(.*)', *(\d+|\[\]), *(\d+), *'(.*)'\.split\('\|'\), *(\d+), *(.*)\)\)",
        r"}\('(.*)', *(\d+|\[\]), *(\d+), *'(.*)'\.split\('\|'\)",
    ]
    for juicer in juicers:
        args = re.search(juicer, source, re.DOTALL)
        if args:
            a = list(args.groups())
            if a[1] == "[]":
                a[1] = 62
            try:
                return a[0], a[3].split("|"), int(a[1]), int(a[2])
            except (ValueError, IndexError):
                raise _UnpackingError("Corrupted p.a.c.k.e.r data.")
    raise _UnpackingError("Could not parse p.a.c.k.e.r structure.")


def _packer_replacestrings(source: str) -> str:
    match = re.search(r'var *(_\w+)=\["(.*?)"\];', source, re.DOTALL)
    if match:
        varname, strings = match.groups()
        startpoint = len(match.group(0))
        lookup = strings.split('","')
        variable = "%s[%%d]" % varname
        for index, value in enumerate(lookup):
            source = source.replace(variable % index, f'"{value}"')
        return source[startpoint:]
    return source


def _packer_unpack(source: str) -> str:
    payload, symtab, radix, count = _packer_filterargs(source)
    if count != len(symtab):
        raise _UnpackingError("Malformed p.a.c.k.e.r symtab.")
    try:
        unbase = _Unbaser(radix)
    except TypeError:
        raise _UnpackingError("Unknown p.a.c.k.e.r encoding.")

    def lookup(match):
        word = match.group(0)
        return symtab[unbase(word)] or word

    payload = payload.replace("\\\\", "\\").replace("\\'", "'")
    source = re.sub(r"\b\w+\b", lookup, payload)
    return _packer_replacestrings(source)


# ---------------------------------------------------------------------------
# Normalizza URL Mixdrop
# ---------------------------------------------------------------------------

def _normalize_mixdrop_url(url: str) -> str:
    """Converte domini alternativi nel dominio cv standard."""
    # Varianti di dominio osservate: mixdrop.club, mixdrop.cfd, m1xdrop.*, m1xdr0p.*
    url = re.sub(r"m1xdr0p\.\w+", "mixdrop.cv", url)
    url = re.sub(r"m1xdrop\.\w+", "mixdrop.cv", url)
    if "mixdrop.club" in url:
        url = url.replace("mixdrop.club", "mixdrop.cv")
    if "mixdrop.cfd" in url:
        url = url.replace("mixdrop.cfd", "mixdrop.cv")
    if "mixdrop.co" in url and "mixdrop.cv" not in url:
        url = url.replace("mixdrop.co", "mixdrop.cv")
    # Normalizza path /emb/ → /e/
    url = url.replace("/emb/", "/e/")
    # Rimuove eventuali suffissi /2... aggiunti da provider
    url = url.split("/2")[0]
    return url


def is_mixdrop_url(url: str) -> bool:
    """Restituisce True se l'URL è un embed Mixdrop (qualsiasi variante di dominio)."""
    return bool(re.search(r"mixdrop|m1xdrop|m[i1]xdr[o0]p", url, re.IGNORECASE))


# ---------------------------------------------------------------------------
# eval_solver: scarica la pagina embed, deoffusca, estrae pattern
# ---------------------------------------------------------------------------

async def _eval_solver(embed_url: str, pattern: re.Pattern) -> Optional[str]:
    headers = {
        "User-Agent": _MIXDROP_UA,
        "Accept-Language": "en-US,en;q=0.5",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=headers)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml", parse_only=SoupStrainer("script"))
        for tag in soup.find_all("script"):
            text = tag.get_text()
            if _packer_detect(text):
                try:
                    unpacked = _packer_unpack(text)
                    m = pattern.search(unpacked)
                    if m:
                        return m.group(1)
                except _UnpackingError as e:
                    logger.debug(f"[Mixdrop] unpack error: {e}")
    except Exception as e:
        logger.warning(f"[Mixdrop] eval_solver error per {embed_url}: {e}")
    return None


# ---------------------------------------------------------------------------
# Funzione pubblica: resolve_mixdrop
# ---------------------------------------------------------------------------

async def resolve_mixdrop(
    embed_url: str,
    content_label: str,
    source_name: str,
    addon_base_url: str,
) -> Optional[dict]:
    """
    Risolve un link embed Mixdrop in uno stream Stremio.

    Args:
        embed_url:       URL embed Mixdrop (es. https://mixdrop.cv/e/abc123)
        content_label:   Titolo del film/episodio
        source_name:     Nome del provider che ha trovato il link (es. "GuardaHD")
        addon_base_url:  Base URL del proxy UFO

    Returns:
        dict stream Stremio oppure None se fallisce.
    """
    embed_url = _normalize_mixdrop_url(embed_url)
    logger.info(f"[Mixdrop] ▶️  risoluzione embed: {embed_url}")

    raw_url = await _eval_solver(embed_url, _WURL_RE)
    if not raw_url:
        logger.warning(f"[Mixdrop] ❌ MDCore.wurl non trovato per {embed_url}")
        return None

    video_url = ("https:" + raw_url) if raw_url.startswith("//") else raw_url
    logger.info(f"[Mixdrop] ✅ URL diretto: {video_url[:100]}")

    # Mixdrop serve MP4 diretto — wrappalo nel proxy con UA Chrome
    playback_headers = {
        "User-Agent": _MIXDROP_UA,
        "Referer": "https://mixdrop.cv/",
    }
    headers_b64 = encode_headers_b64(playback_headers)

    base = addon_base_url.rstrip("/")
    from urllib.parse import quote as _quote
    proxy_url = f"{base}/proxy/manifest.m3u8?url={_quote(video_url, safe='')}&headers={headers_b64}"

    return {
        "name": f"UFO\n🎬 {source_name}",
        "title": f"{content_label}\n▶️ MixDrop",
        "url": proxy_url,
        "behaviorHints": {
            "notWebReady": True,
            "bingeGroup": f"ufo-{source_name.lower()}",
        },
    }
