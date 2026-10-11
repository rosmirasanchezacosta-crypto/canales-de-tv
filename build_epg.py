#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Constructor de guía EPG para KikeFlix.
Combina listas M3U, fuentes XML, scraping en mi.tv y un sistema
de respaldo (fallback) para asegurar que NINGÚN canal quede en blanco.
"""

import sys, os, re, json, gzip, io, time, unicodedata, html, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# ---------- CONFIGURACIÓN DE LISTAS (PRINCIPAL + VIP) ----------
PLAYLIST_URLS = [
    "https://cdn.jsdelivr.net/gh/rosmirasanchezacosta-crypto/canales-de-tv@main/canalesTV2.m3u8",
    "https://raw.githubusercontent.com/rosmirasanchezacosta-crypto/tv-vip/refs/heads/main/EPG-VIP.m3u8"
]

TELEONLINE_JSON = "https://github.com/teleonline/listas/releases/download/epg-latest/epg.json"

XMLTV_SOURCES = {
    "CO": "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz",
    "MX": "https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz",
    "AR": "https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz",
    "ES": "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz",
    "CL": "https://epgshare01.online/epgshare01/epg_ripper_CL1.xml.gz",
    "PE": "https://epgshare01.online/epgshare01/epg_ripper_PE1.xml.gz",
    "UY": "https://epgshare01.online/epgshare01/epg_ripper_UY1.xml.gz",
    "EC": "https://epgshare01.online/epgshare01/epg_ripper_EC1.xml.gz",
    "PLEX": "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz",
}

MITV_CHANNELS = {
    "amc": "amc",
    "cartoon network": "cartoon-network",
    "tnt": "tnt",
    "space": "space",
    "caracol": "caracol",
    "rcn": "rcn",
    "fx": "fx",
    "cinemax": "cinemax",
    "hbo": "hbo",
    "hbo 2": "hbo-2",
    "hbo plus": "hbo-plus",
    "hbo family": "hbo-family",
    "hbo signature": "hbo-signature",
    "hbo pop": "hbo-pop",
    "studio universal": "studio-universal",
    "universal tv": "universal-channel",
    "axn": "axn",
    "sony": "sony-channel",
    "sony movies": "sony-movies",
    "warner channel": "warner-channel",
    "tnt series": "tnt-series",
    "tnt novelas": "tnt-novelas",
    "golden": "golden",
    "golden edge": "golden-edge",
    "Paramount": "paramount-network",
    "mtv": "mtv",
    "htv": "htv",
    "tlc": "tlc",
    "discovery": "discovery-channel",
    "animal planet": "animal-planet",
    "history": "history",
    "espn 2": "espn-2",
    "espn 3": "espn-3",
    "espn 4": "espn-4",
    "espn 5": "espn-5",
    "espn 6": "espn-6",
    "win-sports": "win-sports",
    "win sports+": "win-sports-plus",
    "telecaribe": "telecaribe",
    "star channel": "star-channel"
}

WIN_PAST_H = 2       # Horas hacia atrás
WIN_FUTURE_H = 24    # Horas hacia adelante
MAX_PROG_PER_CH = 20 # Límite de programas por canal

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- NORMALIZACIÓN ----------
_STOP = re.compile(r"\b(hd|fhd|uhd|4k|sd|latino|latam|oficial|live|en vivo|tv|canal|senal|vip)\b")
def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = _STOP.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def log(*a):
    print(*a, flush=True)

# ---------- DESCARGA ----------
def fetch(url, cache_name):
    path = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(path) and os.path.getsize(path) > 100:
        log("   (cache)", cache_name)
        return path
    log("   descargando", cache_name, "...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KikeFlixEPG"})
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return path

# ---------- FECHAS ----------
_XMLTV_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:\s*([+-])(\d{2})(\d{2}))?")
def parse_date_ms(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v if v > 1e12 else v * 1000)
    s = str(v).strip()
    m = _XMLTV_RE.match(s)
    if m:
        y, mo, d, h, mi, se, sign, oh, om = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se), tzinfo=timezone.utc)
            ms = int(dt.timestamp() * 1000)
            if sign:
                off = (int(oh) * 60 + int(om)) * 60000
                ms += -off if sign == "+" else off
            return ms
        except Exception:
            return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def limpiar(txt):
    s = str(txt or "")
    s = html.unescape(html.unescape(s))
    return s.strip()

# ---------- PLAYLISTS ----------
def cargar_playlists():
    chans = []
    for i, url in enumerate(PLAYLIST_URLS):
        try:
            fname = f"lista_{i}.m3u8"
            path = fetch(url, fname)
            txt = open(path, "r", encoding="utf-8", errors="replace").read()
            for ln in txt.splitlines():
                if not ln.startswith("#EXTINF"):
                    continue
                name = ln.split(",", 1)[1].strip() if "," in ln else ""
                tid = (re.search(r'tvg-id="([^"]*)"', ln) or [None, ""])[1]
                tname = (re.search(r'tvg-name="([^"]*)"', ln) or [None, ""])[1]
                keys = set(k for k in (norm(name), norm(tname), norm(tid)) if k)
                if keys:
                    chans.append({"name": name, "keys": keys})
        except Exception as ex:
            log(f"    Error al cargar playlist {url}:", ex)
    return chans

def add_prog(index, keys, s, e, t, d):
    t = limpiar(t)
    if not keys or s is None or e is None or e <= s or not t:
        return
    for k in keys:
        bucket = index.setdefault(k, {})
        if s not in bucket:
            bucket[s] = {"s": s, "e": e, "t": t, "d": limpiar(d)}

# ---------- PARSERS ----------
def parse_teleonline(path, index, need_keys):
    import ijson
    n_ch, n_pr = 0, 0
    with open(path, "rb") as f:
        for ch in ijson.items(f, "channels.item"):
            ids = [ch.get("id"), ch.get("name")] + list(ch.get("display_names") or [])
            keys = set(k for k in (norm(x) for x in ids) if k) & need_keys
            if not keys:
                continue
            n_ch += 1
            for p in (ch.get("programmes") or []):
                s = parse_date_ms(p.get("start"))
                e = parse_date_ms(p.get("stop"))
                t = p.get("title") or p.get("name") or ""
                if isinstance(t, list): t = (t[0] if t else "")
                if isinstance(t, dict): t = t.get("value") or t.get("#text") or ""
                add_prog(index, keys, s, e, str(t), str(p.get("desc") or p.get("description") or ""))
                n_pr += 1
    log(f"    teleonline: {n_ch} canales cruzados, {n_pr} programas")

def parse_xmltv_gz(path, index, need_keys, tag):
    with gzip.open(path, "rb") as f:
        data = f.read()
    root = ET.fromstring(data)
    chan_keys = {}
    for ch in root.findall("channel"):
        cid = ch.get("id") or ""
        names = [cid] + [dn.text or "" for dn in ch.findall("display-name")]
        keys = set(k for k in (norm(x) for x in names) if k) & need_keys
        if keys:
            chan_keys[cid] = keys
    n_pr = 0
    for pr in root.findall("programme"):
        cid = pr.get("channel") or ""
        kept = chan_keys.get(cid)
        if not kept:
            continue
        s = parse_date_ms(pr.get("start"))
        e = parse_date_ms(pr.get("stop"))
        tnode = pr.find("title")
        t = (tnode.text if tnode is not None else "") or ""
        dnode = pr.find("desc")
        d = (dnode.text if dnode is not None else "") or ""
        add_prog(index, kept, s, e, t, d)
        n_pr += 1
    log(f"    {tag}: {len(chan_keys)} canales cruzados, {n_pr} programas")

def parse_mitv(index, need_keys):
    if not BS4_AVAILABLE:
        return
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    bogota_tz = timezone(timedelta(hours=-5))
    hoy = datetime.now(bogota_tz)

    for ch_name, slug in MITV_CHANNELS.items():
        k_norm = norm(ch_name)
        matched_keys = set(nk for nk in need_keys if nk == k_norm or k_norm in nk)
        if not matched_keys:
            continue

        url = f"https://mi.tv/co/canales/{slug}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                html_doc = r.read().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(html_doc, 'html.parser')
            items = soup.select(".broadcasts li")
            if not items:
                continue

            progs_parsed = []
            for item in items:
                time_tag = item.select_one(".time")
                title_tag = item.select_one("h2")
                desc_tag = item.select_one(".synopsis")

                if not time_tag or not title_tag:
                    continue

                t_str = time_tag.get_text(strip=True)
                title_str = title_tag.get_text(strip=True)
                desc_str = desc_tag.get_text(strip=True) if desc_tag else ""

                try:
                    h, m = map(int, t_str.split(":"))
                    dt_prog = datetime(hoy.year, hoy.month, hoy.day, h, m, tzinfo=bogota_tz)
                    progs_parsed.append({"dt": dt_prog, "title": title_str, "desc": desc_str})
                except Exception:
                    continue

            for idx in range(len(progs_parsed)):
                curr = progs_parsed[idx]
                s_ms = int(curr["dt"].timestamp() * 1000)
                if idx + 1 < len(progs_parsed):
                    e_dt = progs_parsed[idx + 1]["dt"]
                    if e_dt < curr["dt"]: e_dt += timedelta(days=1)
                    e_ms = int(e_dt.timestamp() * 1000)
                else:
                    e_ms = s_ms + (2 * 3600 * 1000)

                add_prog(index, matched_keys, s_ms, e_ms, curr["title"], curr["desc"])
        except Exception:
            continue

# ---------- RELLENO DE RESPALDO (FALLBACK) ----------
def aplicar_relleno_respaldo(index, canales):
    """Garantiza que ningún canal de tus M3U quede sin guía en el JSON."""
    now_dt = datetime.now(timezone.utc)
    n_rellenados = 0
    for c in canales:
        tiene_guia = any(k in index and len(index[k]) > 0 for k in c["keys"])
        if not tiene_guia:
            ch_name = c.get("name", "Canal TV")
            for h_offset in range(0, 24, 4):
                s_ms = int((now_dt + timedelta(hours=h_offset)).timestamp() * 1000)
                e_ms = int((now_dt + timedelta(hours=h_offset + 4)).timestamp() * 1000)
                add_prog(index, c["keys"], s_ms, e_ms, f"Transmisión en Vivo - {ch_name}", "Programación general del canal.")
            n_rellenados += 1
    log(f"    Respaldo: {n_rellenados} canales completados con guía por defecto")

# ---------- MAIN ----------
def main():
    log("[1/4] Playlists (TV principal + VIP)...")
    canales = cargar_playlists()
    need_keys = set()
    for c in canales:
        need_keys |= c["keys"]
    log(f"    Total canales detectados: {len(canales)} ({len(need_keys)} claves)")

    index = {}

    log("[2/4] Fuente teleonline...")
    try:
        tj = fetch(TELEONLINE_JSON, "epg_test.json")
        parse_teleonline(tj, index, need_keys)
    except Exception as ex:
        log("    AVISO teleonline fallo:", ex)

    log("[3/4] Fuentes epgshare01 + mi.tv Colombia...")
    for tag, url in XMLTV_SOURCES.items():
        try:
            p = fetch(url, f"src_{tag}.xml.gz")
            parse_xmltv_gz(p, index, need_keys, tag)
        except Exception as ex:
            log(f"    AVISO {tag} fallo:", ex)
            
    parse_mitv(index, need_keys)
    
    # Relleno de seguridad para garantizar 100% cobertura
    aplicar_relleno_respaldo(index, canales)

    log("[4/4] Recortando ventana temporal y escribiendo epg-mini.json...")
    now = int(time.time() * 1000)
    lo = now - WIN_PAST_H * 3600_000
    hi = now + WIN_FUTURE_H * 3600_000
    out = {}
    for k, bucket in index.items():
        progs = [p for p in bucket.values() if p["e"] > lo and p["s"] < hi]
        progs.sort(key=lambda p: p["s"])
        if progs:
            out[k] = progs[:MAX_PROG_PER_CH]

    cubiertos = sum(1 for c in canales if any(k in out for k in c["keys"]))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canales_lista": len(canales),
        "canales_con_epg": cubiertos,
        "channels": out,
    }
    outp = os.path.join(CACHE_DIR, "epg-mini.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(outp)
    log(f"\nOK -> epg-mini.json ({sz/1024:.0f} KB)")
    log(f"COBERTURA: {cubiertos}/{len(canales)} canales con guia ({round(100*cubiertos/len(canales)) if canales else 0}%)")

if __name__ == "__main__":
    main()
