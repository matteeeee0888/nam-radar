#!/usr/bin/env python3
"""
build.py — costruisce la dashboard da data/catalog.jsonl (+ reach_cache.json se c'e').

    python3 tools/build.py                 # rigenera docs/index.html
    python3 tools/build.py --media         # scarica anche le anteprime in locale
    python3 tools/build.py --media-max 400

Le anteprime della Ad Library scadono dopo qualche settimana: con --media vengono
copiate in docs/media/ e la dashboard resta leggibile anche a distanza di mesi.
"""
import argparse, json, math, os, re, sys, unicodedata
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adlib import CATALOG, DATA, ROOT, load_competitors

DOCS = os.path.join(ROOT, "docs")
MEDIA = os.path.join(DOCS, "media")

TEMI = [
    ("HR e persone",        r"\b(hr|risorse umane|people|talent acquisition|recruit|selezion|payroll|diversity|dei\b|welfare|formazione aziendale)"),
    ("AI",                  r"\b(a\.?i\.?|intelligenza artificiale|artificial intelligence|prompt|machine learning|generativ|gpt|copilot)"),
    ("Marketing e comms",   r"\b(marketing|comunicazione|social media|advertis|brand|seo|growth|e-?commerce|copywriting)"),
    ("Data",                r"\b(data|dati|analytics|business intelligence|big data|statistic)"),
    ("Coding e tech",       r"\b(coding|developer|sviluppat|program|full ?stack|java|python|cyber|cloud|software|informatic)"),
    ("Design e UX",         r"\b(ux|ui\b|design|user experience|grafic)"),
    ("Finanza e controllo", r"\b(finanz|amministrazione|controllo di gestione|contabil|fiscal|bilancio|banking|wealth)"),
    ("Management e MBA",    r"\b(mba|management|leadership|general management|executive|imprend|project manag|strateg)"),
    ("Sostenibilità ESG",   r"\b(esg|sostenibil|green|energia|ambient|csr)"),
    ("Sanità e pharma",     r"\b(sanit|pharma|salute|healthcare|medic)"),
    ("Legale",              r"\b(legal|diritto|avvocat|giurid|complian)"),
]

LEVE = [
    ("Borsa di studio", r"\b(borsa di studio|borse di studio|scholarship|agevolazion|sconto|early bird|prezzo bloccato)"),
    ("Placement",       r"\b(placement|garanzia lavoro|trova lavoro|assunz|carriera|stage|tirocin|job)"),
    ("Scadenza",        r"\b(ultimi giorni|iscrizioni in scadenza|posti limitati|ultimi posti|chiudono|entro il|scade)"),
    ("Part-time",       r"\b(part.?time|weekend|serale|compatibil|mentre lavori|senza lasciare)"),
    ("Online",          r"\b(online|a distanza|da remoto|live streaming|blended)"),
    ("Testimonianza",   r"\b(ho fatto|mi ha cambiat|la mia storia|ex studente|alumni|testimonian|racconta)"),
    ("Autorevolezza",   r"\b(docenti|accredit|riconosciut|universit|classifica|ranking|partner|aziende partner)"),
]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def tag(text, table):
    t = norm(text)
    return [nome for nome, pat in table if re.search(pat, t)]


def landing_key(u):
    if not u:
        return ""
    u = re.sub(r"[?#].*$", "", u.lower())
    return re.sub(r"^https?://(www\.)?", "", u).rstrip("/")


def concept_key(r):
    """Stesso messaggio sulla stessa destinazione = stesso concetto."""
    testo = norm((r.get("titolo") or "") + " " + (r.get("copy") or ""))[:90]
    return f"{r['competitor_slug']}|{testo}|{landing_key(r.get('landing_url'))}"


def lognorm(x, uno, tetto):
    """log normalizzato: vale 1.0 al valore di riferimento, con un tetto."""
    if not x or x <= 0:
        return 0.0
    return min(math.log(1 + x) / math.log(1 + uno), tetto)


def scarica_media(rows, cap):
    """
    Di un carosello si scaricano tutte le slide, non la prima.
    La prima slide di un carosello e' l'aggancio; il resto e' l'argomentazione, ed e'
    la parte che si va a vedere quando si vuole capire come e' costruita l'offerta.
    """
    from urllib.request import Request, urlopen
    os.makedirs(MEDIA, exist_ok=True)
    # Se l'immagine e' gia' pubblicata come webp non serve riscaricare l'originale:
    # in cloud la cartella degli originali riparte vuota a ogni giro, e senza questo
    # controllo si riscaricherebbero duemila file per ricodificarli identici.
    pubblicato = os.environ.get("RADAR_SITE")
    fatti = 0
    for r in rows:
        if fatti >= cap:
            break
        urls = (r.get("image_urls") or [])[:6]
        locali = []
        for n, url in enumerate(urls):
            fp = os.path.join(MEDIA, f"{r['ad_archive_id']}-{n}.jpg")
            rel = f"media/{r['ad_archive_id']}-{n}.jpg"
            if os.path.exists(fp) and os.path.getsize(fp) > 1000:
                locali.append(rel)
                continue
            if pubblicato and os.path.exists(
                    os.path.join(pubblicato, "media", f"{r['ad_archive_id']}-{n}.webp")):
                locali.append(rel)
                continue
            try:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=25) as resp:
                    b = resp.read()
            except Exception:
                continue
            if len(b) > 800:
                open(fp, "wb").write(b)
                locali.append(rel)
                fatti += 1
        if locali:
            r["thumbs"] = locali
            r["thumb"] = locali[0]
    print(f"  immagini scaricate: {fatti}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", action="store_true", help="scarica le anteprime in locale")
    ap.add_argument("--media-max", type=int, default=800)
    a = ap.parse_args()

    if not os.path.exists(CATALOG):
        sys.exit("catalogo vuoto: lancia prima tools/fetch.py")
    rows = [json.loads(l) for l in open(CATALOG) if l.strip()]
    cache_fp = os.path.join(DATA, "reach_cache.json")
    reach = json.load(open(cache_fp)) if os.path.exists(cache_fp) else {}
    cfg = load_competitors()
    cliente = (cfg.get("cliente") or {}).get("brand", "noi")
    # il nome con cui ci chiamiamo puo' cambiare dopo la raccolta: comanda competitors.json
    for r in rows:
        if r.get("tier") == "cliente":
            r["competitor"] = cliente

    # ---- concetti: quante inserzioni diverse spingono lo stesso messaggio
    famiglia = defaultdict(list)
    for r in rows:
        famiglia[concept_key(r)].append(r)

    chiavi = {k: f"c{i}" for i, k in enumerate(sorted(famiglia))}
    for r in rows:
        k = concept_key(r)
        r["concetto"] = chiavi[k]
        fam = famiglia[k]
        r["slot_concetto"] = sum(x.get("collation_count") or 1 for x in fam)
        r["ads_concetto"] = len(fam)
        attive = r.get("ads_attive_brand") or len([x for x in rows if x["competitor_slug"] == r["competitor_slug"]])
        r["quota_portafoglio"] = round(100.0 * r["slot_concetto"] / max(attive, 1), 1)
        d = r.get("giorni_in_aria") or 0
        r["trazione"] = round(
            1.0 * lognorm(d, 180, 2.0) +
            1.5 * lognorm(r["slot_concetto"], 5, 2.0) +
            1.5 * lognorm(r["quota_portafoglio"], 5, 2.0) +
            0.4 * lognorm(attive, 100, 1.5), 2)
        info = reach.get(r["ad_archive_id"]) or {}
        r["eu_reach"] = info.get("eu_reach")
        r["reach_giorno"] = int(r["eu_reach"] / max(d, 1)) if r.get("eu_reach") else None
        r["target_eta"] = info.get("target_eta")
        r["target_genere"] = info.get("target_genere")
        r["reach_per_eta"] = info.get("reach_per_eta")
        testo = " ".join([r.get("titolo") or "", r.get("copy") or "",
                          r.get("link_description") or "", r.get("landing_url") or ""])
        r["temi"] = tag(testo, TEMI) or ["Altro"]
        r["leve"] = tag(testo, LEVE)
        u = (r.get("landing_url") or "").lower()
        # fb.me / nessuna destinazione = modulo di contatto dentro Facebook, non un sito:
        # e' una scelta di funnel, e per un cliente che vende master conta saperlo
        r["lead_form"] = (not u) or "fb.me" in u or "facebook.com/leadgen" in u
        r["banda"] = ("scaling" if r["trazione"] >= 3.5
                      else "solida" if r["trazione"] >= 2.2
                      else "in prova")

    if a.media:
        ordinati = sorted(rows, key=lambda r: -(r.get("eu_reach") or 0) if r.get("eu_reach") else -r["trazione"])
        scarica_media(ordinati, a.media_max)
    else:
        for r in rows:
            locali = [f"media/{r['ad_archive_id']}-{n}.jpg" for n in range(6)
                      if os.path.exists(os.path.join(MEDIA, f"{r['ad_archive_id']}-{n}.jpg"))]
            if locali:
                r["thumbs"] = locali
                r["thumb"] = locali[0]
    for r in rows:
        # il video scaricato da fetch_videos.py: quello non scade piu'
        if os.path.exists(os.path.join(MEDIA, f"{r['ad_archive_id']}.mp4")):
            r["video_local"] = f"media/{r['ad_archive_id']}.mp4"

    # ---- riepilogo per competitor
    per_comp = defaultdict(lambda: {"ads": 0, "concetti": set(), "reach": 0, "giorni": [],
                                    "attive": None, "mercato": "", "tipo": "", "tier": ""})
    for r in rows:
        c = per_comp[r["competitor"]]
        c["ads"] += 1
        c["concetti"].add(concept_key(r))
        c["reach"] += r.get("eu_reach") or 0
        if r.get("giorni_in_aria") is not None:
            c["giorni"].append(r["giorni_in_aria"])
        c["attive"] = r.get("ads_attive_brand") or c["attive"]
        c["mercato"], c["tipo"], c["tier"] = r["mercato"], r.get("tipo") or "", r.get("tier") or ""
    riepilogo = []
    for nome, c in per_comp.items():
        g = sorted(c["giorni"])
        riepilogo.append({
            "competitor": nome, "ads": c["ads"], "attive": c["attive"] or c["ads"],
            "concetti": len(c["concetti"]), "reach": c["reach"],
            "mediana_giorni": g[len(g) // 2] if g else 0,
            "mercato": c["mercato"], "tipo": c["tipo"], "tier": c["tier"],
        })
    riepilogo.sort(key=lambda x: (-x["reach"], -x["attive"]))

    campi = ["ad_archive_id", "competitor", "competitor_slug", "mercato", "tier", "tipo",
             "attiva", "in_aria_dal", "giorni_in_aria", "formato", "piattaforme",
             "titolo", "copy", "cta_text", "landing_url", "thumb", "thumbs", "video_urls",
             "slot_concetto", "ads_concetto", "quota_portafoglio", "trazione", "banda",
             "eu_reach", "reach_giorno", "target_eta", "target_genere", "temi", "leve",
             "lead_form", "concetto", "video_local", "attiva_ora", "primo_avvistamento", "ultimo_avvistamento",
             "ads_attive_brand", "n_card"]
    dati = [{k: r.get(k) for k in campi} for r in rows]

    ha_reach = any(r.get("eu_reach") for r in rows)
    payload = {
        "generato": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "generato_iso": date.today().isoformat(),
        "cliente": cliente,
        "ha_reach": ha_reach,
        "ads": dati,
        "riepilogo": riepilogo,
        "temi": sorted({t for r in rows for t in r["temi"]}),
        "leve": sorted({t for r in rows for t in r["leve"]}),
    }
    os.makedirs(DOCS, exist_ok=True)
    html = TEMPLATE.replace("/*__DATI__*/null", json.dumps(payload, ensure_ascii=False))
    open(os.path.join(DOCS, "index.html"), "w").write(html)
    print(f"dashboard: {len(rows)} inserzioni, {len(riepilogo)} inserzionisti, "
          f"{'con' if ha_reach else 'senza'} reach UE -> docs/index.html")


TEMPLATE = r"""<!doctype html>
<html lang="it"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar creatività TAG</title>
<style>
:root{
  --bg:#f6f6f4; --card:#fff; --ink:#15150f; --muted:#6b6b60; --line:#e3e3dc;
  --accent:#1a5f4a; --accent-soft:#e6f0ec; --hot:#b4471f; --hot-soft:#fbeee8;
  --warm:#8a6a1f; --warm-soft:#f7efd9; --radius:12px;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#12120f; --card:#1b1b17; --ink:#f0efe8; --muted:#9a9a8d; --line:#2c2c25;
  --accent:#5fbfa0; --accent-soft:#17302a; --hot:#e88a5f; --hot-soft:#33211a;
  --warm:#d6b25f; --warm-soft:#2e2717;
}}
:root[data-theme="dark"]{
  --bg:#12120f; --card:#1b1b17; --ink:#f0efe8; --muted:#9a9a8d; --line:#2c2c25;
  --accent:#5fbfa0; --accent-soft:#17302a; --hot:#e88a5f; --hot-soft:#33211a;
  --warm:#d6b25f; --warm-soft:#2e2717;
}
*{box-sizing:border-box}
/* una classe con display: vince sull'attributo hidden, e il pannello resta aperto */
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1360px;margin:0 auto;padding:28px 16px 72px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between;margin-bottom:6px}
h1{font-size:25px;margin:0;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.sub.allarme{color:var(--hot);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px;margin:22px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:13px 15px}
.tile .n{font-size:24px;font-weight:650;letter-spacing:-.02em}
.tile .l{color:var(--muted);font-size:12px;margin-top:2px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin-bottom:18px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:9px 0}
.row .lab{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  width:92px;flex:none}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;
  padding:5px 12px;font-size:13px;cursor:pointer;font-family:inherit;transition:.12s}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chip.on.dim{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
.barra{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pieghevole{padding:0;overflow:hidden}
.testa{width:100%;display:flex;align-items:center;gap:10px;background:none;border:0;
  color:var(--ink);font:inherit;padding:12px 16px;cursor:pointer;text-align:left}
.testa:hover{color:var(--accent)}
.testa:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.tit2{font-weight:600;font-size:14px;flex:none}
.sintesi{color:var(--muted);font-size:12.5px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.freccia{color:var(--muted);transition:transform .15s;flex:none}
.testa[aria-expanded="true"] .freccia{transform:rotate(180deg)}
.pieghevole .scroll{padding:0 16px 14px}
.avanzati{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-top:-10px}
.divisore{width:1px;height:22px;background:var(--line)}
.mini{font-size:12.5px;color:var(--muted);display:flex;align-items:center;gap:5px}
.mini input{width:68px;border:1px solid var(--line);background:var(--bg);color:var(--ink);
  border-radius:8px;padding:6px 8px;font:inherit;font-size:13px;font-variant-numeric:tabular-nums}
.nota-inline{font-size:11.5px;color:var(--muted);flex:1;min-width:220px;line-height:1.4}
input[type=search],select{border:1px solid var(--line);background:var(--bg);color:var(--ink);
  border-radius:8px;padding:7px 11px;font:inherit;font-size:13px}
select{min-width:auto;max-width:230px}
#q{flex:1;min-width:180px}
select.attivo{border-color:var(--accent);color:var(--accent);font-weight:600}
.empty{padding:44px 20px;text-align:center;color:var(--muted);line-height:1.7}
.empty b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;
  cursor:pointer;user-select:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.cli td{background:var(--accent-soft);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(282px,1fr));gap:14px}
.ad{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  overflow:hidden;display:flex;flex-direction:column}
.ad .media{aspect-ratio:1/1;background:var(--bg);position:relative;overflow:hidden}
.ad .media img{width:100%;height:100%;object-fit:cover;display:block}
.ad .media video{width:100%;height:100%;object-fit:contain;display:block;background:#0d0d0b}
.ad .media .track{display:flex;height:100%;overflow-x:auto;scroll-snap-type:x mandatory;
  scrollbar-width:none;-ms-overflow-style:none;scroll-behavior:smooth}
.ad .media .track::-webkit-scrollbar{display:none}
.ad .media .track img{flex:0 0 100%;width:100%;height:100%;object-fit:cover;scroll-snap-align:center}
.ad .media .nav{position:absolute;top:50%;transform:translateY(-50%);width:30px;height:30px;
  border:0;border-radius:50%;background:rgba(0,0,0,.6);color:#fff;font-size:19px;line-height:1;
  cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0 0 2px;
  opacity:0;transition:opacity .15s}
.ad .media:hover .nav,.ad .media:focus-within .nav{opacity:1}
.ad .media .nav.p{left:6px}.ad .media .nav.n{right:6px}
.ad .media .nav:focus-visible{opacity:1;outline:2px solid var(--accent);outline-offset:2px}
.ad .media .conta{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.72);color:#fff;
  border-radius:999px;padding:2px 8px;font-size:11px;font-variant-numeric:tabular-nums;
  pointer-events:none}
@media(hover:none){.ad .media .nav{opacity:.85}}
.ad .media:has(video){aspect-ratio:9/16;max-height:480px}
.ad .media .ph{display:flex;align-items:center;justify-content:center;height:100%;
  color:var(--muted);font-size:12px}
.badge{pointer-events:none;position:absolute;top:8px;left:8px;background:rgba(0,0,0,.72);color:#fff;
  border-radius:999px;padding:3px 9px;font-size:11px;letter-spacing:.02em}
.badge.r{left:auto;right:8px;background:var(--hot)}
.badge.v{top:auto;bottom:8px;background:var(--accent)}
.ad .b{padding:12px 13px 13px;display:flex;flex-direction:column;gap:7px;flex:1}
.who{font-size:12px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}
.tit{font-weight:600;font-size:14px;line-height:1.35}
.cpy{font-size:12.5px;color:var(--muted);line-height:1.45;max-height:5.8em;overflow:hidden}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:auto;padding-top:4px}
.tg{font-size:11px;border-radius:999px;padding:2px 8px;background:var(--accent-soft);color:var(--accent)}
.tg.l{background:var(--warm-soft);color:var(--warm)}
.tg.b{background:var(--hot-soft);color:var(--hot)}
.foot{display:flex;justify-content:space-between;align-items:center;gap:8px;
  border-top:1px solid var(--line);padding-top:8px;font-size:12px}
.foot a{color:var(--accent);text-decoration:none}
.foot a:hover{text-decoration:underline}
.metric{font-variant-numeric:tabular-nums;font-weight:600}
.metric .sep{color:var(--muted);font-weight:400;margin:0 1px}
h2{font-size:15px;margin:26px 0 10px;letter-spacing:-.01em}
h2 span{color:var(--muted);font-weight:400;font-size:13px}
.note{color:var(--muted);font-size:12.5px;line-height:1.6;max-width:70ch}
.scroll{overflow-x:auto}
.empty{padding:50px;text-align:center;color:var(--muted)}
button.ghost{border:1px solid var(--line);background:transparent;color:var(--muted);
  border-radius:8px;padding:6px 11px;font:inherit;font-size:12px;cursor:pointer}
@media(max-width:640px){.row .lab{width:100%}.wrap{padding:18px 12px 60px}}
</style></head><body><div class="wrap">

<header>
  <div>
    <h1>Radar creatività TAG</h1>
    <div class="sub" id="sub"></div>
  </div>
  <button class="ghost" onclick="tema()">tema chiaro/scuro</button>
</header>

<div class="tiles" id="tiles"></div>

<div class="panel barra">
  <select id="fMercato" aria-label="Mercato"></select>
  <select id="fComp" aria-label="Inserzionista"></select>
  <select id="fTema" aria-label="Tema"></select>
  <select id="fFormato" aria-label="Formato"></select>
  <select id="ordine" aria-label="Ordinamento"></select>
  <input type="search" id="q" placeholder="cerca nel copy, nel titolo, nella landing">
  <label class="mini"><input type="checkbox" id="raggruppa" checked> una card per concetto</label>
  <button class="ghost" id="piu" aria-expanded="false">altri filtri</button>
  <button class="ghost" id="azzera" hidden>azzera</button>
</div>

<div class="panel avanzati" id="avanzati" hidden>
  <label class="mini">Leva <select id="fLeva"></select></label>
  <label class="mini">Destinazione <select id="fDest"></select></label>
  <label class="mini">Stato <select id="fStato"></select></label>
  <label class="mini">In aria da <select id="minGiorni"></select></label>
  <label class="mini">Chi &egrave; <select id="fTipo"></select></label>
  <span class="divisore"></span>
  <label class="mini">CPM &euro; <input type="number" id="cpm" value="8" min="1" max="60" step="0.5"></label>
  <label class="mini">frequenza <input type="number" id="freq" value="1.8" min="1" max="6" step="0.1"></label>
  <span class="nota-inline">spesa &asymp; persone raggiunte &times; frequenza &times; CPM / 1000 &mdash; metti qui il CPM e la frequenza reali del nostro account per tarare</span>
</div>

<div class="panel pieghevole">
  <button class="testa" id="apriTab" aria-expanded="false">
    <span class="tit2">Chi spinge</span>
    <span class="sintesi" id="sintesiTab"></span>
    <span class="freccia">&#9662;</span>
  </button>
  <div class="scroll" id="boxTab" hidden><table id="tab"></table></div>
</div>

<h2 id="hGriglia">Le creativit&agrave; che reggono</h2>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" hidden></div>

<h2>Come si legge</h2>
<div class="panel note" id="metodo"></div>

</div><script>
const D = /*__DATI__*/null;
// Ogni filtro e' un valore solo, "" = nessun filtro. Prima erano Set pilotati da chip
// che non si coloravano mai: si accumulavano selezioni invisibili e la lista si svuotava
// senza che si capisse perche'. Con i select lo stato e' quello che vedi.
const VUOTO = {mercato:"", comp:"", tema:"", formato:"", leva:"", dest:"",
               stato:"attive", q:"", minGiorni:14, ordine:"auto"};
const S = {...VUOTO, cpm:8, freq:1.8};
const fmt = n => (n==null?"—":n.toLocaleString("it-IT"));
const eur = n => n==null?"—":(n>=1e6?"€"+(n/1e6).toFixed(1).replace(".",",")+"M"
                   :n>=1e3?"€"+Math.round(n/1e3)+"k":"€"+Math.round(n));
// Stessa aritmetica della dashboard interna, al contrario: li' il CPM esce da
// spesa/(impression/1000) sui nostri dati; qui la spesa esce dal CPM che gli diamo.
// La reach e' gente raggiunta una volta: le impression sono reach x frequenza.
const spesa = a => a.eu_reach ? (a.eu_reach * S.freq * S.cpm / 1000) : null;
const K = n => n==null?"—":(n>=1e6?(n/1e6).toFixed(1).replace(".",",")+"M":n>=1e3?Math.round(n/1e3)+"k":n);

function tema(){const r=document.documentElement;
  const cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  r.setAttribute("data-theme",cur==="dark"?"light":"dark");}

function sel(id, chiave, voci){
  const e = document.getElementById(id);
  e.innerHTML = voci.map(([v,l]) => `<option value="${v}">${l}</option>`).join("");
  e.value = S[chiave];
  e.addEventListener("change", () => { S[chiave] = e.value; render(); });
}
function elenco(campo, etichettaTutti){
  const v = [...new Set(D.ads.flatMap(a => Array.isArray(a[campo]) ? a[campo] : [a[campo]]))]
              .filter(Boolean).sort((x,y)=>x.localeCompare(y,"it"));
  return [["", etichettaTutti], ...v.map(x=>[x,x])];
}

const REGOLE = {
  mercato: (a,v) => v==="IT" ? a.mercato==="IT" : a.mercato!=="IT",
  comp:    (a,v) => a.competitor===v,
  tema:    (a,v) => (a.temi||[]).includes(v),
  formato: (a,v) => a.formato===v,
  leva:    (a,v) => (a.leve||[]).includes(v),
  tipo:    (a,v) => (a.tipo==="cliente"?"noi":a.tipo)===v,
  dest:    (a,v) => v==="lead" ? !!a.lead_form : !a.lead_form,
  stato:   (a,v) => v==="attive" ? a.attiva_ora!==false : a.attiva_ora===false,
};
function passa(a, salta){
  for(const k in REGOLE){
    if(k===salta || !S[k] || (k==="stato" && S.stato==="tutte")) continue;
    if(!REGOLE[k](a, S[k])) return false;
  }
  if(salta!=="minGiorni" && (a.giorni_in_aria||0) < +S.minGiorni) return false;
  if(S.q && salta!=="q"){
    const h=((a.titolo||"")+" "+(a.copy||"")+" "+(a.landing_url||"")+" "+a.competitor).toLowerCase();
    if(!h.includes(S.q.toLowerCase())) return false;
  }
  return true;
}
// quali filtri sono accesi, per il pulsante azzera e per spiegare una lista vuota
function attivi(){
  const nomi = {mercato:"mercato", comp:"inserzionista", tema:"tema", formato:"formato",
                leva:"leva", tipo:"chi è", dest:"destinazione", q:"ricerca"};
  const out = Object.keys(nomi).filter(k=>S[k]).map(k=>nomi[k]);
  if(S.stato!=="attive") out.push("stato");
  if(+S.minGiorni!==14) out.push("in aria da");
  return out;
}

const ORD = {
  auto:   (a,b)=> D.ha_reach ? (b.eu_reach||0)-(a.eu_reach||0) || b.trazione-a.trazione
                             : b.trazione-a.trazione,
  reach:  (a,b)=> (b.eu_reach||0)-(a.eu_reach||0),
  giorno: (a,b)=> (b.reach_giorno||0)-(a.reach_giorno||0),
  traz:   (a,b)=> b.trazione-a.trazione,
  durata: (a,b)=> (b.giorni_in_aria||0)-(a.giorni_in_aria||0),
  nuove:  (a,b)=> (a.giorni_in_aria||0)-(b.giorni_in_aria||0),
};

function raggruppa(lista){
  // Un concetto girato su dodici visual non e' dodici creativita': e' una, spinta forte.
  // La card mostra la variante piu' rappresentativa e dice quante ne stanno dietro.
  const per = new Map();
  for(const a of lista){
    const k = a.concetto || a.ad_archive_id;
    const cur = per.get(k);
    const peso = x => (x.eu_reach||0)*1e6 + (x.giorni_in_aria||0);
    if(!cur || peso(a) > peso(cur.capo)) per.set(k, {capo:a, n:(cur?cur.n:0)+1, reach:(cur?cur.reach:0)+(a.eu_reach||0)});
    else { cur.n++; cur.reach += (a.eu_reach||0); }
  }
  return [...per.values()].map(g => ({...g.capo, varianti:g.n,
                                      eu_reach: g.reach || g.capo.eu_reach}));
}

function render(){
  let vis = D.ads.filter(passa);
  if(document.getElementById("raggruppa").checked) vis = raggruppa(vis);
  vis = vis.sort(ORD[S.ordine]||ORD.auto);

  // riquadri in alto
  const comps = new Set(vis.map(a=>a.competitor));
  const reachTot = vis.reduce((s,a)=>s+(a.eu_reach||0),0);
  const lunghe = vis.filter(a=>(a.giorni_in_aria||0)>=90).length;
  const video = vis.filter(a=>a.formato==="video").length;
  const grp = document.getElementById("raggruppa").checked;
  const t=[[grp?"Concetti in aria":"Creatività in aria",fmt(vis.length)],["Inserzionisti",fmt(comps.size)],
           ["Vive da 90+ giorni",fmt(lunghe)],
           ["Quota video",vis.length?Math.round(100*video/vis.length)+"%":"—"]];
  if(D.ha_reach){
    t.splice(2,0,["Persone raggiunte (UE)",K(reachTot)]);
    t.splice(3,0,["Spesa stimata",eur(reachTot*S.freq*S.cpm/1000)]);
  }
  document.getElementById("tiles").innerHTML =
    t.map(([l,n])=>`<div class="tile"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

  // tabella inserzionisti
  const nomi=[...comps];
  const righe = D.riepilogo.filter(r=>nomi.includes(r.competitor)).map(r=>{
    const sue = vis.filter(a=>a.competitor===r.competitor);
    const creativita = sue.reduce((n,a)=>n+(a.varianti||1),0);
    const reach = sue.reduce((s,a)=>s+(a.eu_reach||0),0);
    return {...r, viste:creativita, reachVis:reach,
            concetti:new Set(sue.map(a=>a.concetto)).size,
            mediana: sue.length? sue.map(a=>a.giorni_in_aria||0).sort((x,y)=>x-y)[Math.floor(sue.length/2)] : 0};
  }).sort((a,b)=> D.ha_reach ? b.reachVis-a.reachVis : b.attive-a.attive);
  const unMercato = new Set(D.ads.map(a=>a.mercato)).size <= 1;
  document.getElementById("tab").innerHTML =
    `<thead><tr><th>Inserzionista</th>${unMercato?"":"<th>Mercato</th>"}<th>Tipo</th>
      <th class="num">Attive su Meta</th><th class="num">Nel radar</th>
      <th class="num">Concetti</th><th class="num">Mediana giorni</th>
      ${D.ha_reach?'<th class="num">Persone raggiunte</th><th class="num">Spesa stimata</th>':''}</tr></thead><tbody>` +
    righe.map(r=>`<tr class="${r.tier==='cliente'?'cli':''}">
      <td>${r.tier==="cliente"?"NOI · "+r.competitor:r.competitor}</td>${unMercato?"":`<td>${r.mercato}</td>`}<td>${r.tipo==="cliente"?"noi":(r.tipo||"—")}</td>
      <td class="num">${fmt(r.attive)}</td><td class="num">${fmt(r.viste)}</td>
      <td class="num">${fmt(r.concetti)}</td><td class="num">${r.mediana}</td>
      ${D.ha_reach?`<td class="num">${K(r.reachVis)}</td><td class="num">${eur(r.reachVis*S.freq*S.cpm/1000)}</td>`:''}</tr>`).join("") + "</tbody>";

  const testa = righe.slice(0,3).map(r=>r.competitor).join(", ");
  document.getElementById("sintesiTab").textContent =
    `${righe.length} inserzionisti` + (testa ? ` · in testa ${testa}` : "");

  // griglia
  document.getElementById("hGriglia").innerHTML =
    `Le creatività che reggono <span>— ${vis.length} in ordine di ${
      {auto:D.ha_reach?"spesa stimata":"trazione",reach:"persone raggiunte",giorno:"persone raggiunte al giorno",
       traz:"trazione",durata:"durata",nuove:"freschezza"}[S.ordine||"auto"]}</span>`;
  document.getElementById("grid").innerHTML = vis.slice(0,240).map(card).join("");

  // un filtro acceso si vede: sul select e sul pulsante azzera
  [["fMercato","mercato"],["fComp","comp"],["fTema","tema"],["fFormato","formato"],
   ["fLeva","leva"],["fTipo","tipo"],["fDest","dest"],["fMercato","mercato"]].forEach(([id,k])=>{
     const e=document.getElementById(id); if(e) e.classList.toggle("attivo", !!S[k]);
   });
  const acc = attivi();
  document.getElementById("azzera").hidden = acc.length===0;

  const vuoto = document.getElementById("empty");
  vuoto.hidden = vis.length>0;
  if(!vis.length){
    // se e' vuoto si dice quale filtro toglie tutto, invece di lasciare la pagina muta
    const colpevoli = acc.filter(n=>{
      const k = {mercato:"mercato",inserzionista:"comp",tema:"tema",formato:"formato",
                 leva:"leva","chi è":"tipo",destinazione:"dest",ricerca:"q",
                 stato:"stato","in aria da":"minGiorni"}[n];
      return D.ads.filter(a=>passa(a,k)).length > 0;
    });
    vuoto.innerHTML = acc.length
      ? `Nessuna creatività con questi filtri.<br>`
        + (colpevoli.length ? `Prova a togliere <b>${colpevoli.join("</b> o <b>")}</b>.<br>` : "")
        + `<button class="ghost" style="margin-top:12px" onclick="reset()">azzera i filtri</button>`
      : "Nessuna creatività raccolta.";
  }
}

function card(a){
  const imgs = (window.MEDIA||{})[a.ad_archive_id] || a.thumbs || (a.thumb?[a.thumb]:[]);
  const vsrc = (window.VIDEO||{})[a.ad_archive_id] || a.video_local;
  const media = vsrc
    ? `<video controls preload="none" playsinline${imgs[0]?` poster="${imgs[0]}"`:""} src="${vsrc}"></video>`
    : imgs.length > 1
      ? `<div class="track">${imgs.map(u=>`<img loading="lazy" src="${u}" alt="">`).join("")}</div>
         <button class="nav p" aria-label="Slide precedente">&lsaquo;</button>
         <button class="nav n" aria-label="Slide successiva">&rsaquo;</button>
         <span class="conta">1/${imgs.length}</span>`
      : imgs.length === 1 ? `<img loading="lazy" src="${imgs[0]}" alt="">`
          : `<div class="ph">anteprima non salvata</div>`;
  const rb = a.eu_reach ? `<span class="badge r">${K(a.eu_reach)} raggiunti</span>` : "";
  const host = a.landing_url ? (a.landing_url.replace(/^https?:\/\/(www\.)?/,"").split("/")[0]) : "";
  const dest = a.lead_form
    ? `<span style="color:var(--muted)" title="modulo di contatto dentro Facebook">lead form</span>`
    : (a.landing_url?`<a href="${a.landing_url}" target="_blank" rel="noopener" title="${esc(a.landing_url)}">${esc(host)}</a>`:"");
  const tags = [...(a.temi||[]).map(t=>`<span class="tg">${t}</span>`),
                ...(a.leve||[]).map(t=>`<span class="tg l">${t}</span>`),
                `<span class="tg b">${a.banda}</span>`].join("");
  return `<article class="ad">
    <div class="media">${media}<span class="badge">${a.formato}${a.n_card>1?" · "+a.n_card:""}</span>${rb}
      ${a.varianti>1?`<span class="badge v">${a.varianti} varianti</span>`:""}</div>
    <div class="b">
      <div class="who"><span>${a.tier==="cliente"?"NOI · "+a.competitor:a.competitor}</span><span>${a.attiva_ora===false?"spenta · ":""}${a.giorni_in_aria} gg</span></div>
      ${a.titolo?`<div class="tit">${esc(a.titolo)}</div>`:""}
      ${a.copy?`<div class="cpy">${esc(a.copy)}</div>`:""}
      <div class="tags">${tags}</div>
      <div class="foot">
        <span class="metric">${a.eu_reach
            ? `${eur(spesa(a))} stimati <span class="sep">·</span> ${K(a.reach_giorno)} persone/gg`
            : `trazione ${a.trazione}`}</span>
        <span>
          ${a.cta_text?`<span style="color:var(--muted)">${esc(a.cta_text)}</span> · `:""}
          <a href="https://www.facebook.com/ads/library/?id=${a.ad_archive_id}" target="_blank" rel="noopener"
             title="Apri l'inserzione nella Libreria inserzioni di Meta">Libreria inserzioni &nearr;</a>
          ${dest?" · "+dest:""}
        </span>
      </div>
    </div></article>`;
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

function init(){
  // La pagina promette "ogni lunedi'": se la raccolta e' invecchiata deve dirlo, non
  // lasciare la promessa in piedi mentre mostra dati vecchi.
  const giorni = D.generato_iso
    ? Math.floor((Date.now() - new Date(D.generato_iso + "T00:00:00")) / 86400000) : 0;
  const sub = document.getElementById("sub");
  sub.textContent =
    `${D.ads.length} creatività attive raccolte dalla Meta Ad Library · ` +
    (giorni > 8 ? `ultima raccolta ${D.generato}: l'aggiornamento settimanale non è passato`
                : "si aggiorna ogni lunedì") +
    (D.ha_reach ? " · con reach UE dichiarata da Meta" : " · reach UE non ancora richiesta");
  sub.classList.toggle("allarme", giorni > 8);
  sub.title = `Ultima raccolta: ${D.generato}`;

  // un filtro con una sola voce e' rumore: compare solo se c'e' davvero altro
  const mercati = new Set(D.ads.map(a=>a.mercato));
  const boxMercato = document.getElementById("fMercato");
  if(mercati.size > 1){
    sel("fMercato","mercato",[["","Tutti i mercati"],["IT","Italia"],["estero","Estero"]]);
  } else {
    boxMercato.hidden = true;
  }
  sel("fComp","comp",elenco("competitor","Tutti gli inserzionisti"));
  sel("fTema","tema",[["","Tutti i temi"],...D.temi.map(t=>[t,t])]);
  sel("fFormato","formato",elenco("formato","Tutti i formati"));
  sel("fLeva","leva",[["","Tutte le leve"],...D.leve.map(t=>[t,t])]);
  sel("fTipo","tipo",[["","Chiunque"],["noi","Noi"],
                      ...[...new Set(D.ads.map(a=>a.tipo).filter(t=>t&&t!=="cliente"))].sort().map(t=>[t,t])]);
  sel("fDest","dest",[["","Ovunque"],["lead","Lead form"],["sito","Sito"]]);
  sel("fStato","stato",[["attive","In aria adesso"],["tutte","Anche le spente"],["spente","Solo spente"]]);
  sel("minGiorni","minGiorni",[["0","da qualsiasi tempo"],["14","da 2+ settimane"],
                               ["30","da 30+ giorni"],["60","da 60+ giorni"],["90","da 90+ giorni"]]);
  sel("ordine","ordine", D.ha_reach
    ? [["auto","ordina per spesa stimata"],["giorno","persone raggiunte al giorno"],
       ["traz","trazione"],["durata","più longeve"],["nuove","appena lanciate"]]
    : [["auto","ordina per trazione"],["durata","più longeve"],["nuove","appena lanciate"]]);

  document.getElementById("metodo").innerHTML = D.ha_reach ? METODO_REACH : METODO_PROXY;
  render();
}

function reset(){
  Object.assign(S, VUOTO);
  ["fMercato","fComp","fTema","fFormato","fLeva","fTipo","fDest","fStato","minGiorni","ordine"]
    .forEach(id=>{ const e=document.getElementById(id); if(e) e.value = S[{
      fMercato:"mercato",fComp:"comp",fTema:"tema",fFormato:"formato",fLeva:"leva",
      fTipo:"tipo",fDest:"dest",fStato:"stato",minGiorni:"minGiorni",ordine:"ordine"}[id]];
    });
  document.getElementById("q").value = "";
  render();
}

const METODO_REACH = `
<p><b>Reach UE</b> è il numero di persone raggiunte che Meta stessa pubblica per ogni inserzione
servita nell'Unione Europea, per obbligo del DSA. Non è una stima nostra e non è spesa:
è gente raggiunta, cumulata da quando l'inserzione è partita.</p>
<p><b>Spesa stimata</b> non e' un dato di Meta: e' un conto che facciamo noi, con la stessa
aritmetica della dashboard interna presa al contrario. Li' il CPM esce dai nostri numeri veri
(spesa diviso impression per mille); qui il CPM lo diamo noi e ne ricaviamo la spesa:</p>
<p style="font-family:ui-monospace,monospace;font-size:12px">spesa &asymp; persone raggiunte &times; frequenza &times; CPM / 1000</p>
<p>Le persone raggiunte le pubblica Meta; frequenza e CPM no, quindi sono i due campi in alto
e si cambiano a mano. I valori di partenza (CPM 8 &euro;, frequenza 1,8) sono un ordine di
grandezza, non il nostro dato: <b>appena ci metti il CPM e la frequenza reali del nostro
account, la colonna diventa attendibile</b> — e resta comunque una stima, perche' il CPM di un
competitor non e' il nostro.</p>
<p><b>Persone al giorno</b> divide la reach per i giorni in aria. Serve a non premiare
un'inserzione solo perché è vecchia: è la cosa più vicina alla pressione attuale che si
possa osservare senza avere l'account di chi la paga. Due avvertenze: un pubblico più largo
costa meno a persona, e chi ha più frequenza raggiunge meno gente con gli stessi soldi.</p>
<p><b>Trazione</b> resta come secondo metro dove la reach manca (inserzioni fuori UE): combina
da quanto tempo è in aria, quante varianti dello stesso concetto girano, che quota del
portafoglio attivo del brand occupano.</p>
<p><b>Concetto</b> = stesso messaggio sulla stessa destinazione. Se un brand ha 60 inserzioni
attive e 18 stanno su un concetto solo, quel concetto si prende il 30% del suo sforzo. È il
segnale più onesto di "questo funziona per loro".</p>`;
const METODO_PROXY = `
<p>La reach UE non è ancora stata richiesta: la dashboard ordina per <b>trazione</b>, che combina
da quanto tempo un'inserzione è in aria, quante varianti dello stesso concetto girano, e che
quota del portafoglio attivo del brand occupano. Tutto in scala logaritmica: la differenza fra
30 e 60 giorni conta più di quella fra 400 e 430.</p>
<p><b>Concetto</b> = stesso messaggio sulla stessa destinazione. Se un brand ha 60 inserzioni
attive e 18 stanno su un concetto solo, quel concetto si prende il 30% del suo sforzo creativo.</p>
<p>Per ordinare sui numeri veri di Meta (persone raggiunte nell'UE, età, genere, paesi) lancia
<code>tools/enrich_reach.py</code>: serve la chiave ScrapeCreators nel .env.</p>`;

document.getElementById("q").addEventListener("input",e=>{S.q=e.target.value;render();});
document.getElementById("raggruppa").addEventListener("change",render);
document.getElementById("azzera").addEventListener("click",reset);
document.getElementById("apriTab").addEventListener("click",e=>{
  const box=document.getElementById("boxTab"), apri=box.hidden;
  box.hidden=!apri;
  e.currentTarget.setAttribute("aria-expanded",String(apri));
});
document.getElementById("piu").addEventListener("click",e=>{
  const box=document.getElementById("avanzati"), apri=box.hidden;
  box.hidden=!apri; e.target.setAttribute("aria-expanded",String(apri));
  e.target.textContent = apri ? "meno filtri" : "altri filtri";
});

// Delega sulla griglia: le card si ridisegnano a ogni filtro, i gestori no.
document.getElementById("grid").addEventListener("click", e => {
  const b = e.target.closest(".nav"); if(!b) return;
  const t = b.parentElement.querySelector(".track"); if(!t) return;
  // Per indice, non per delta: con scroll-snap mandatory uno scrollBy da una posizione
  // gia' agganciata viene riassorbito dallo snap e il clic sembra non fare niente.
  const n = t.children.length, w = t.clientWidth;
  const i = Math.round(t.scrollLeft / w);
  const j = Math.max(0, Math.min(n - 1, i + (b.classList.contains("n") ? 1 : -1)));
  t.scrollTo({left: j * w, behavior: "smooth"});
  const c = b.parentElement.querySelector(".conta");
  if (c) c.textContent = `${j + 1}/${n}`;
});
document.getElementById("grid").addEventListener("scroll", e => {
  const t = e.target; if(!t.classList || !t.classList.contains("track")) return;
  const c = t.parentElement.querySelector(".conta"); if(!c) return;
  const n = t.children.length;
  c.textContent = `${Math.min(n, Math.round(t.scrollLeft / t.clientWidth) + 1)}/${n}`;
}, true);
document.getElementById("cpm").addEventListener("input",e=>{S.cpm=+e.target.value||8;render();});
document.getElementById("freq").addEventListener("input",e=>{S.freq=+e.target.value||1.8;render();});
init();
</script></body></html>
"""

if __name__ == "__main__":
    main()
