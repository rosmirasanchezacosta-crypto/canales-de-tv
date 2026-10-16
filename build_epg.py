#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Constructor de guia EPG para KikeFlix.
Descarga varias fuentes publicas (teleonline + epgshare01 por pais + intv + videostreaming),
las cruza SOLO contra los canales de la lista m3u8 y produce un archivo
pequeno 'epg-mini.json' (ventana de ahora + proximas horas).

Salida: epg-mini.json  (mapa  claveNormalizada -> [ {s,e,t,d}, ... ] )
  s = inicio (ms epoch)   e = fin (ms epoch)   t = titulo   d = descripcion
"""
import sys, os, re, json, gzip, io, time, unicodedata, html, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ---------- CONFIG ----------
# Dos listas: la principal de TV y la de canales VIP (tarjetas de la pagina).
PLAYLIST_URLS = [
    "https://cdn.jsdelivr.net/gh/rosmirasanchezacosta-crypto/canales-de-tv@main/canalesTV2.m3u8",
    "https://raw.githubusercontent.com/rosmirasanchezacosta-crypto/tv-vip/refs/heads/main/EPG-VIP.m3u8",
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

# ---------- FUENTE VIDEOSTREAMING (Canal propio) ----------
VIDEOSTREAMING_EPG = "https://app.videostreaming.net/m/epg/97f0d5114a"
VIDEOSTREAMING_MAP = {
    "97f0d5114a": ["KIKEFLIX-CINE", "KikeFlix Cine", "kikeflix-cine"]
}

# ---------- FUENTE intv.com.co (parrilla real por canal, texto) ----------
MAPA_INTV = {
    "star-channel": ["STAR CHANEL", "Star Channel"],
    "sony-movies": ["SONY MOVIES"],
    "usa": ["EE.UU.", "Red de EE.UU.", "USA Network"],
    "nick": ["NIKELODEON", "NICK"],
    "cartoonito": ["CARTOONNITO"],
    "cartoon-network": ["CARTOON NETWORK", "cartoon network"],
    "warner-channel": ["WARNER-2", "WARNER CHANNEL", "WARNER"],
    "paramount-channel": ["PARAMOUNT", "PARAMOUNT NETWORK"],
    "space": ["SPACE ESPANOL", "SPACE 2", "SPACE", "Space"],
    "espn-4": ["SPN 4", "ESPN 4"],
    "espn-5": ["SPN 5", "ESPN 5"],
    "espn-6": ["SPN 6", "ESPN 6"],
    "dsports": ["DIRECTV DEPORTES", "DIRECTV SPORTS"],
    "dsports2": ["DIRECTV SPORTS 2", "DIRECTV SPORTS2"],
    "win-sports": ["WIN SPORTS", "WIN FUTBOL"],
    "telecaribe": ["TELECARIBE", "TELECARIBE PLUS"],
    "distrito-comedia": ["DISTRITO COMEDIA"],
    "24-horas": ["24 Horas"],
    "cine-mexicano": ["CINE MEXICANO"],
    "vive-mundial-2": ["VIVE MUNDIAL2"],
    "todo-cine": ["TODO CINE"],
    "canela-tv": ["CANELA TV"],
    "space-2": ["SPACE 2"],
    "sony": ["SONY"],
    "discovery-kids": ["DISCOVERY KIDS"],
    "reino-infantil": ["Reino Infantil"],
    "tv-kids-latam": ["TV Kids LatAm"],
    "mr-bean-anime": ["Mr Bean Anime"],
    "amc": ["AMC"],
    "disney-jr": ["DISNEY JUNIOR"],
    "disney-channel": ["DISNEY CHANNEL"],
    "xtremacartoons": ["XTREMACARTOONS"],
    "cinecanal": ["CINECANAL"],
    "a-and-e": ["A&E"],
    "hbo-family": ["HBO FAMILY"],
    "hbo-signature": ["HBO SIGNATURE"],
    "hbo-plus": ["HBO PLUS"],
    "hbo-pop": ["HBO POP"],
    "hbo-2": ["HBO 2"],
    "hbo": ["HBO"],
    "axn": ["AXN"],
    "mtv": ["MTV"],
    "mtv-venganza-de-los-ex": ["MTV VENGANZA DE LOS EX"],
    "golden": ["GOLDEN"],
    "golden-edge": ["GOLDEN EDGE"],
    "studio-universal": ["STUDIO UNIVERSAL"],
    "universal-tv": ["UNIVERSAL TV"],
    "fx": ["FX"],
    "tlc": ["TLC"],
    "runtime": ["Runtime"],
    "national-geographic": ["NATIONAL GEOGRAPHIC"],
    "discovery": ["DISCOVERY CHANNEL"],
    "animal-planet": ["ANIMAL PLANET"],
    "history": ["HISTORY CHANNEL"],
    "historia": ["HISTORIA"],
    "tyc-sports": ["TYC SPORTS"],
    "espn-2": ["ESPN 2"],
    "espn-3": ["ESPN 3"],
    "win-sports-plus": ["WIN SPORTS+"],
    "rcn": ["RCN"],
    "caracol": ["CARACOL"],
    "tnt": ["TNT"],
    "tnt-series": ["TNT SERIES"],
    "tnt-novelas": ["TNT NOVELAS"],
    "cine-adrenalina": ["CINE ADRENALINA"],
    "cine-terror": ["CINE TERROR"],
    "accion-plus": ["ACCION+"],
    "k-dramas": ["K-DRAMAS"],
    "dramas-coreanos": ["Dramas Coreanos"],
    "cine-premium": ["CINE PREMIUM"],
    "tv-misterio": ["TV MISTERIO"],
}
INTV_BASE = "https://intv.com.co/inicio/television/canales/{slug}/?nc={ts}"

# Ventana temporal a conservar (para que el archivo quede pequeno)
WIN_PAST_H = 1        # horas hacia atras
WIN_FUTURE_H = 30     # horas hacia adelante
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

# ---------- PLAYLIST ----------
def _parse_m3u(path):
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

def cargar_playlists():
    """Descarga y combina todas las listas de PLAYLIST_URLS (principal + VIP).
    De-duplica por nombre normalizado para no contar un canal dos veces."""
    chans = []
    vistos = set()
    for i, url in enumerate(PLAYLIST_URLS):
        try:
            p = fetch(url, f"lista_{i}.m3u8")
        except Exception as ex:
            log(f"    AVISO lista {i} fallo:", ex)
            continue
        for c in _parse_m3u(p):
            firma = tuple(sorted(c["keys"]))
            if firma in vistos:
                continue
            vistos.add(firma)
            chans.append(c)
    return chans

# ---------- LIMPIEZA DE TEXTO ----------
def limpiar(txt):
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

def _intv_fetch(slug):
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = ""
    for intento in range(4):
        url = INTV_BASE.format(slug=slug, ts=int(time.time() * 1000) + intento)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Accept-Language": "es-CO,es;q=0.9",
            "Cache-Control": "no-cache",
        })
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                last = r.read().decode("utf-8", "replace")
        except Exception:
            time.sleep(0.5)
            continue
        if _INTV_ROW.search(last):
            return last
        time.sleep(0.5)
    return last

_INTV_ROW = re.compile(r'<tr class="intv-program-row"([^>]*)>')
def parse_intv(index, need_keys):
    n_ch = 0
    n_pr = 0
    for slug, aliases in MAPA_INTV.items():
        keys = set()
        for a in aliases:
            k = norm(a)
            if k and k in need_keys:
                keys.add(k)
        if not keys:
            continue
        try:
            htmlpg = _intv_fetch(slug)
        except Exception as ex:
            log(f"    AVISO intv {slug} fallo:", ex)
            continue
        rows = []
        for m in _INTV_ROW.finditer(htmlpg):
            a = m.group(1)
            gs = re.search(r'data-start="(\d+)"', a)
            ge = re.search(r'data-end="(\d+)"', a)
            gp = re.search(r'data-program="([^"]*)"', a)
            if not (gs and gp):
                continue
            s = int(gs.group(1)) * 1000
            e = int(ge.group(1)) * 1000 if ge else None
            rows.append([s, e, html.unescape(gp.group(1))])
        if not rows:
            continue
        rows.sort(key=lambda r: r[0])
        for i, r in enumerate(rows):
            if r[1] is None or r[1] <= r[0]:
                r[1] = rows[i + 1][0] if i + 1 < len(rows) else r[0] + 3600_000
        for k in keys:
            index[k] = {}
        n_ch += 1
        for s, e, t in rows:
            add_prog(index, keys, s, e, t, "")
            n_pr += 1
    log(f"    intv.com.co: {n_ch} canales cruzados, {n_pr} programas")

# ---------- FUENTE VIDEOSTREAMING (KIKEFLIX CINE) ----------
def parse_videostreaming(index, need_keys):
    """Extrae la guia real de window.VS_EPG para KikeFlix Cine (videostreaming.net)"""
    n_ch = 0
    n_pr = 0
    
    keys = set()
    for aliases in VIDEOSTREAMING_MAP.values():
        for a in aliases:
            k = norm(a)
            if k and k in need_keys:
                keys.add(k)

    if not keys:
        return

    try:
        req = urllib.request.Request(
            VIDEOSTREAMING_EPG, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            html_content = r.read().decode("utf-8", "replace")

        match = re.search(r'window\.VS_EPG\s*=\s*(\{.*?\});', html_content, re.DOTALL)
        if not match:
            log("    AVISO videostreaming: No se encontro el objeto window.VS_EPG")
            return

        vs_data = json.loads(match.group(1))
        timeline = vs_data.get("timeline", [])
        if not timeline:
            return

        # Ajuste de zona horaria Colombia (UTC-5)
        tz_cot = timezone(timedelta(hours=-5))
        now_cot = datetime.now(tz_cot)
        today_str = now_cot.strftime("%Y-%m-%d")

        # Limpiar guia anterior para sobreescribir con la real
        for k in keys:
            index[k] = {}

        n_ch += 1
        for item in timeline:
            nombre = item.get("nombre") or "Programa"
            desde_str = item.get("desde") or "00:00"
            hasta_str = item.get("hasta") or "00:00"

            try:
                dt_s = datetime.fromisoformat(f"{today_str}T{desde_str}:00-05:00")
                dt_e = datetime.fromisoformat(f"{today_str}T{hasta_str}:00-05:00")

                s_ms = int(dt_s.timestamp() * 1000)
                e_ms = int(dt_e.timestamp() * 1000)

                # Si cruza la medianoche (ej. 23:00 a 02:00)
                if e_ms <= s_ms:
                    e_ms += 24 * 3600 * 1000

                sub_desc = f"Categoria: {item.get('key', '')}"
                add_prog(index, keys, s_ms, e_ms, nombre, sub_desc)
                n_pr += 1
            except Exception:
                continue

        log(f"    videostreaming.net: {n_ch} canal cruzado, {n_pr} programas")

    except Exception as ex:
        log("    AVISO videostreaming.net fallo:", ex)

# ---------- MAIN ----------
def main():
    log("[1/6] Playlists (TV principal + VIP)...")
    canales = cargar_playlists()
    need_keys = set()
    for c in canales:
        need_keys |= c["keys"]
    log(f"    {len(canales)} canales en total, {len(need_keys)} claves de busqueda")

    index = {}

    log("[2/6] Fuente teleonline (Espana/global)...")
    try:
        tj = fetch(TELEONLINE_JSON, "epg_test.json")
        parse_teleonline(tj, index, need_keys)
    except Exception as ex:
        log("    AVISO teleonline fallo:", ex)

    log("[3/6] Fuentes epgshare01 por pais...")
    for tag, url in XMLTV_SOURCES.items():
        try:
            p = fetch(url, f"src_{tag}.xml.gz")
            parse_xmltv_gz(p, index, need_keys, tag)
        except Exception as ex:
            log(f"    AVISO {tag} fallo:", ex)

    log("[4/6] Fuente intv.com.co (parrilla real por canal)...")
    try:
        parse_intv(index, need_keys)
    except Exception as ex:
        log("    AVISO intv.com.co fallo:", ex)

    log("[5/6] Fuente videostreaming.net (canal propio KIKEFLIX CINE)...")
    try:
        parse_videostreaming(index, need_keys)
    except Exception as ex:
        log("    AVISO videostreaming.net fallo:", ex)

    log("[6/6] Recortando ventana temporal y escribiendo salida...")
    now = int(time.time() * 1000)
    lo = now - WIN_PAST_H * 3600_000
    hi = now + WIN_FUTURE_H * 3600_000
    out = {}
    for k, bucket in index.items():
        progs = [p for p in bucket.values() if p["e"] > lo and p["s"] < hi]
        progs.sort(key=lambda p: p["s"])
        
        futuros = [p for p in progs if p["e"] > now]
        actuales = [p for p in progs if p["e"] <= now]
        progs = (actuales[-1:] if actuales else []) + futuros
        progs = progs[:MAX_PROG_PER_CH]
        if progs:
            out[k] = progs

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
    log(f"COBERTURA: {cubiertos}/{len(canales)} canales con guia real ({round(100*cubiertos/len(canales)) if canales else 0}%)")

if __name__ == "__main__":
    main()
