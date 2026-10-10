#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Constructor de guia EPG para KikeFlix.
Descarga varias fuentes publicas (teleonline + epgshare01 por pais),
las cruza SOLO contra los canales de la lista m3u8 y produce un archivo
pequeno 'epg-mini.json' (ventana de ahora + proximas horas).

Salida: epg-mini.json  (mapa  claveNormalizada -> [ {s,e,t,d}, ... ] )
  s = inicio (ms epoch)   e = fin (ms epoch)   t = titulo   d = descripcion
"""
import sys, os, re, json, gzip, io, time, unicodedata, html, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------- CONFIG ----------
PLAYLIST_URL = "https://cdn.jsdelivr.net/gh/rosmirasanchezacosta-crypto/canales-de-tv@main/canalesTV2.m3u8"

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

# Ventana temporal a conservar (para que el archivo quede pequeno)
WIN_PAST_H = 2        # horas hacia atras
WIN_FUTURE_H = 24     # horas hacia adelante
MAX_PROG_PER_CH = 20  # tope de programas por canal

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- NORMALIZACION (debe coincidir con el JS de la pagina) ----------
_STOP = re.compile(r"\b(hd|fhd|uhd|4k|sd|latino|latam|oficial|live|en vivo|tv|canal|senal)\b")
def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = _STOP.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def log(*a):
    print(*a, flush=True)

# ---------- DESCARGA con cache local ----------
def fetch(url, cache_name, binary=True):
    path = os.path.join(CACHE_DIR, cache_name)
    if os.path.exists(path) and os.path.getsize(path) > 100:
        log("  (cache)", cache_name)
        return path
    log("  descargando", cache_name, "...")
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

# ---------- PLAYLIST ----------
def cargar_playlist(path):
    chans = []
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
    return chans

# ---------- LIMPIEZA DE TEXTO ----------
def limpiar(txt):
    # Decodifica entidades HTML que vienen mal en las fuentes (p.ej. &#8211; -> -,
    # &amp; -> &). Se hace dos veces por si vienen doblemente codificadas
    # (&amp;#8211;). Asi el titulo guarda el caracter real y la pagina lo muestra bien.
    s = str(txt or "")
    s = html.unescape(html.unescape(s))
    return s.strip()

# ---------- INDICE DE FUENTES ----------
def add_prog(index, keys, s, e, t, d):
    t = limpiar(t)
    if not keys or s is None or e is None or e <= s or not t:
        return
    for k in keys:
        bucket = index.setdefault(k, {})
        if s not in bucket:
            bucket[s] = {"s": s, "e": e, "t": t, "d": limpiar(d)}

def parse_teleonline(path, index, need_keys):
    import ijson
    n_ch = 0
    n_pr = 0
    with open(path, "rb") as f:
        for ch in ijson.items(f, "channels.item"):
            ids = [ch.get("id"), ch.get("name")] + list(ch.get("display_names") or [])
            keys = set(k for k in (norm(x) for x in ids) if k)
            keys &= need_keys
            if not keys:
                continue
            n_ch += 1
            for p in (ch.get("programmes") or []):
                s = parse_date_ms(p.get("start"))
                e = parse_date_ms(p.get("stop"))
                t = p.get("title") or p.get("name") or ""
                if isinstance(t, list):
                    t = (t[0] if t else "")
                if isinstance(t, dict):
                    t = t.get("value") or t.get("#text") or ""
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
        keys = set(k for k in (norm(x) for x in names) if k)
        kept = keys & need_keys
        if kept:
            chan_keys[cid] = kept
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

# ---------- MAIN ----------
def main():
    log("[1/4] Playlist...")
    pl_path = fetch(PLAYLIST_URL, "lista.m3u8")
    canales = cargar_playlist(pl_path)
    need_keys = set()
    for c in canales:
        need_keys |= c["keys"]
    log(f"    {len(canales)} canales en la lista, {len(need_keys)} claves de busqueda")

    index = {}

    log("[2/4] Fuente teleonline (Espana/global)...")
    try:
        tj = fetch(TELEONLINE_JSON, "epg_test.json")
        parse_teleonline(tj, index, need_keys)
    except Exception as ex:
        log("    AVISO teleonline fallo:", ex)

    log("[3/4] Fuentes epgshare01 por pais...")
    for tag, url in XMLTV_SOURCES.items():
        try:
            p = fetch(url, f"src_{tag}.xml.gz")
            parse_xmltv_gz(p, index, need_keys, tag)
        except Exception as ex:
            log(f"    AVISO {tag} fallo:", ex)

    log("[4/4] Recortando ventana temporal y escribiendo salida...")
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
    log(f"COBERTURA: {cubiertos}/{len(canales)} canales con guia real ({round(100*cubiertos/len(canales))}%)")

if __name__ == "__main__":
    main()
