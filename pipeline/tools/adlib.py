#!/usr/bin/env python3
"""
adlib.py — il livello che parla con la Meta Ad Library pubblica.

Non si legge il DOM. La pagina della Ad Library incorpora un payload JSON (e poi lo
impagina via GraphQL) molto piu' ricco di quello che disegna a schermo: da li' arrivano
la data esatta di partenza, il conteggio di famiglia che fa Meta stessa, la URL di
destinazione gia' pulita e il totale di inserzioni attive dell'inserzionista.

Cosa NON c'e' qui dentro, per nessuna inserzione commerciale: spend, impressions,
reactions. E per le inserzioni UE il dato di reach c'e' ma sta sulla scheda di
dettaglio, dietro una chiamata separata: lo prende enrich_reach.py.
"""
import json, os, random, re, time, unicodedata
from datetime import date, datetime, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BASE = ("https://www.facebook.com/ads/library/?active_status={status}&ad_type=all"
        "&country={country}&media_type=all")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
CATALOG = os.path.join(DATA, "catalog.jsonl")
COMPETITORS = os.path.join(ROOT, "competitors.json")


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "untitled"


def pause(lo=2.0, hi=3.5):
    time.sleep(random.uniform(lo, hi))


def ts_to_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return None


def days_since(iso, ref=None):
    if not iso:
        return None
    try:
        d0 = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(((ref or date.today()) - d0).days, 0)


def load_competitors():
    with open(COMPETITORS) as f:
        return json.load(f)


def save_competitors(cfg):
    with open(COMPETITORS, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ------------------------------------------------------------------ api

API = "https://api.scrapecreators.com/v1/facebook/adLibrary"


def api_get(percorso, key, timeout=60):
    """Una GET su ScrapeCreators. Serve dove il browser non arriva: da un IP di
    datacenter la Ad Library pubblica risponde male, e l'API impagina sul serio."""
    from urllib.request import Request, urlopen
    req = Request(f"{API}/{percorso}", headers={"x-api-key": key, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------ payload

def find_connections(obj, out):
    if isinstance(obj, dict):
        c = obj.get("search_results_connection")
        if isinstance(c, dict) and "edges" in c:
            out.append(c)
        for v in obj.values():
            find_connections(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_connections(v, out)
    return out


def parse_blob(text):
    conns = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            conns += find_connections(json.loads(line), [])
        except json.JSONDecodeError:
            continue
    return conns


def embedded(page):
    return page.evaluate("""() => {
      const out = [];
      for (const s of document.querySelectorAll('script[type="application/json"]')) {
        if (s.textContent.includes('search_results_connection')) out.push(s.textContent);
      }
      return out;
    }""")


# ------------------------------------------------------------------ normalizzazione

def media_of(snap):
    vids = snap.get("videos") or []
    imgs = snap.get("images") or []
    cards = snap.get("cards") or []
    fmt = (snap.get("display_format") or "").upper()
    if vids or any(c.get("video_preview_image_url") or c.get("video_hd_url") for c in cards):
        mt = "video"
    elif imgs or cards:
        mt = "immagine"
    else:
        mt = "n.d."
    if len(cards) > 1 and mt != "video":
        mt = "carosello"
    return mt, fmt


def pick_urls(snap):
    v, i = [], []
    for x in (snap.get("videos") or []):
        for k in ("video_hd_url", "video_sd_url"):
            if x.get(k):
                v.append(x[k])
                break
        if x.get("video_preview_image_url"):
            i.append(x["video_preview_image_url"])
    for x in (snap.get("images") or []):
        u = x.get("resized_image_url") or x.get("original_image_url")
        if u:
            i.append(u)
    for c in (snap.get("cards") or []):
        for k in ("video_preview_image_url", "resized_image_url", "original_image_url"):
            if c.get(k):
                i.append(c[k])
                break
        for k in ("video_hd_url", "video_sd_url"):
            if c.get(k):
                v.append(c[k])
                break
    return v[:3], i[:6]


def _text(x):
    if isinstance(x, dict):
        x = x.get("text") or (x.get("markup") or {}).get("__html") if isinstance(x.get("markup"), dict) else x.get("text")
    return re.sub(r"<[^>]+>", " ", str(x or "")).replace("&nbsp;", " ").strip()


def body_text(snap):
    b = _text(snap.get("body"))
    if not b:
        for c in (snap.get("cards") or []):
            if c.get("body"):
                b = _text(c["body"])
                break
    return re.sub(r"\s+\n", "\n", b)


def _is_template(t):
    """Le ads DCO espongono segnaposto tipo {{product.name}}: il testo vero sta nelle card."""
    return not t or "{{" in t


def normalize(node, comp, country):
    snap = node.get("snapshot") or {}
    start = ts_to_iso(node.get("start_date"))
    mt, fmt = media_of(snap)
    vids, imgs = pick_urls(snap)
    link = snap.get("link_url") or ""
    title = _text(snap.get("title"))
    copy = body_text(snap)
    desc = _text(snap.get("link_description"))
    for c in (snap.get("cards") or []):
        if not link and c.get("link_url"):
            link = c["link_url"]
        if _is_template(title) and c.get("title") and not _is_template(_text(c["title"])):
            title = _text(c["title"])
        if _is_template(copy) and c.get("body") and not _is_template(_text(c["body"])):
            copy = _text(c["body"])
        if _is_template(desc) and c.get("link_description") and not _is_template(_text(c["link_description"])):
            desc = _text(c["link_description"])
    title = "" if _is_template(title) else title
    copy = "" if _is_template(copy) else copy
    desc = "" if _is_template(desc) else desc
    return {
        "ad_archive_id": str(node.get("ad_archive_id") or ""),
        "collation_id": str(node.get("collation_id") or ""),
        # quante inserzioni Meta stessa raggruppa in questa famiglia di creativo
        "collation_count": node.get("collation_count") or 1,
        "attiva": bool(node.get("is_active")),
        "page_id": str(node.get("page_id") or snap.get("page_id") or ""),
        "competitor": comp["nome"],
        "competitor_slug": comp["slug"],
        "mercato": comp.get("mercato") or country,
        "tier": comp.get("tier"),
        "tipo": comp.get("tipo"),
        "page_like_count": snap.get("page_like_count"),
        "in_aria_dal": start,
        "fine": ts_to_iso(node.get("end_date")),
        "giorni_in_aria": days_since(start),
        "piattaforme": [p.title() for p in (node.get("publisher_platform") or [])],
        "formato": mt,
        "display_format": fmt,
        "video_urls": vids,
        "image_urls": imgs,
        "titolo": title,
        "copy": copy,
        "link_description": desc,
        "cta_text": snap.get("cta_text"),
        "cta_type": snap.get("cta_type"),
        "landing_url": link or None,
        "n_card": len(snap.get("cards") or []),
        "country_query": country,
        "raccolto_il": date.today().isoformat(),
    }


# ------------------------------------------------------------------ browser

def launch(p, headful=False):
    try:
        b = p.chromium.launch(channel="chrome", headless=not headful)
    except Exception:
        b = p.chromium.launch(headless=not headful)
    ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 1000},
                        locale="it-IT")
    return b, ctx.new_page()


def harvest(page, comp, country, target=150, quiet_max=6):
    """Scorre la lista e raccoglie, sia dal payload incorporato sia dal GraphQL."""
    seen, rows, captured = set(), [], []

    def on_response(resp):
        if "/api/graphql" in resp.url:
            try:
                captured.append(resp.text())
            except Exception:
                pass

    page.on("response", on_response)

    def drain(blobs):
        added = 0
        for c in blobs:
            for conn in c:
                for e in conn.get("edges") or []:
                    for n in (e.get("node") or {}).get("collated_results") or []:
                        aid = str(n.get("ad_archive_id") or "")
                        if not aid or aid in seen:
                            continue
                        seen.add(aid)
                        rows.append(normalize(n, comp, country))
                        added += 1
        return added

    total = None
    for txt in embedded(page):
        try:
            for conn in find_connections(json.loads(txt), []):
                if total is None:
                    total = conn.get("count")
        except json.JSONDecodeError:
            pass
    drain([parse_blob(t) for t in embedded(page)])

    # La lista e' a caricamento pigro: Meta aggiunge una pagina per volta quando il fondo
    # entra nel viewport. Uno scrollBy secco spesso non basta - si arriva a 30 e ci si
    # ferma - quindi si va in fondo, si aspetta la rete, e si insiste anche sui giri
    # vuoti: capita che una pagina arrivi in ritardo dopo due tentativi a mano vuota.
    quiet = 0
    while len(rows) < target and quiet < quiet_max:
        before = len(rows)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        pause(0.6, 1.0)
        page.evaluate("window.scrollBy(0, -400)")
        pause(0.4, 0.7)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        try:
            page.wait_for_load_state("networkidle", timeout=9000)
        except Exception:
            pass
        pause(1.2, 2.0)
        blobs = [parse_blob(t) for t in captured]
        captured.clear()
        drain(blobs)
        drain([parse_blob(t) for t in embedded(page)])
        quiet = 0 if len(rows) - before else quiet + 1

    page.remove_listener("response", on_response)
    return rows[:target], total
