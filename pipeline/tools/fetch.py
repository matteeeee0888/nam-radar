#!/usr/bin/env python3
"""
fetch.py — raccoglie le inserzioni attive di ogni competitor dalla Ad Library pubblica.

    PY=~/tools/Scrapling/.venv/bin/python
    $PY tools/fetch.py --resolve          # riempie i page_id mancanti in competitors.json
    $PY tools/fetch.py                    # raccolta su tutti i competitor attivi
    $PY tools/fetch.py --only luiss-bs --limit 200
    $PY tools/fetch.py --mercato IT       # solo il mercato italiano

Scrive data/raw/<slug>-<data>.jsonl (lo storico, non si tocca) e rigenera
data/catalog.jsonl con l'ultimo raccolto di ogni competitor.
"""
import argparse, json, os, re, sys, time
from datetime import date, timedelta
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adlib import (BASE, CATALOG, RAW, api_get, harvest, launch, load_competitors,
                   normalize, pause, save_competitors, slugify, embedded,
                   find_connections, parse_blob)
from enrich_reach import load_key


def page_url(page_id, country, status="active"):
    return BASE.format(status=status, country=country) + \
           f"&view_all_page_id={page_id}&search_type=page"


def resolve_one(page, nome, country):
    """Cerca il nome come keyword e tiene l'inserzionista che gli somiglia di piu'."""
    url = BASE.format(status="active", country=country) + \
          f"&q={quote(nome)}&search_type=keyword_unordered"
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    pause(3.5, 5.0)
    tally = {}
    for txt in embedded(page):
        for conn in parse_blob(txt):
            for e in conn.get("edges") or []:
                for n in (e.get("node") or {}).get("collated_results") or []:
                    snap = n.get("snapshot") or {}
                    pid = str(n.get("page_id") or snap.get("page_id") or "")
                    nm = snap.get("page_name") or ""
                    if not pid:
                        continue
                    k = (pid, nm)
                    tally[k] = tally.get(k, 0) + 1
    if not tally:
        return None
    want = slugify(nome)

    def grado(nm):
        s = slugify(nm)
        if s == want:
            return 3                      # stesso nome: e' lui
        if s.startswith(want + "-"):
            return 2                      # "Ironhack Italia": lui, con un suffisso
        return 0                          # "Restaurant Le Wagon": un omonimo, non lui

    cand = [((pid, nm), n) for (pid, nm), n in tally.items() if grado(nm)]
    if not cand:
        return None
    (pid, nm), n = max(cand, key=lambda it: (grado(it[0][1]), it[1]))
    return {"page_id": pid, "nome_pagina": nm, "ads_viste": n}


def raccogli_keyword(parole, paese, per_parola, key, cfg):
    """
    Raccolta a partire dalle parole invece che dalle pagine.

    Serve dove i competitor non hanno un marchio da inseguire: un installatore di
    caldaie, un e-commerce nato sei mesi fa. La lista di pagine li' nasce gia' vecchia;
    la domanda no. Qui l'inserzionista non lo decidiamo noi, lo trova la ricerca, e il
    set si rinfresca da solo a ogni giro.
    """
    noti = {c["page_id"]: c for c in cfg["competitors"] if c.get("page_id")}
    righe, visti = [], set()
    for parola in parole:
        cursore, presi = None, 0
        while presi < per_parola:
            q = (f"search/ads?query={quote(parola)}&country={paese}"
                 f"&status=ACTIVE&media_type=ALL")
            if cursore:
                q += "&cursor=" + quote(cursore)
            d = None
            for tentativo in range(3):
                try:
                    d = api_get(q, key)
                    break
                except Exception:
                    time.sleep(2 + tentativo * 3)
            if d is None:
                break
            res = d.get("searchResults") or d.get("results") or []
            nuovi = 0
            for n in res:
                aid = str(n.get("ad_archive_id") or "")
                if not aid or aid in visti:
                    continue
                snap = n.get("snapshot") or {}
                pid = str(n.get("page_id") or snap.get("page_id") or "")
                nome = snap.get("page_name") or n.get("page_name") or "?"
                base = noti.get(pid) or {}
                comp = {"slug": base.get("slug") or slugify(nome),
                        "nome": base.get("nome") or nome,
                        "mercato": base.get("mercato") or paese,
                        "tier": base.get("tier") or "scoperto",
                        "tipo": base.get("tipo") or (snap.get("page_categories") or ["n.d."])[0]}
                visti.add(aid)
                r = normalize(n, comp, paese)
                r["parola"] = parola
                righe.append(r)
                nuovi += 1
                presi += 1
            cursore = d.get("cursor")
            if not nuovi or not cursore:
                break
            time.sleep(0.3)
        print(f"   {parola:36} {presi:>4} inserzioni", flush=True)
    return righe


def raccogli_api(comp, paese, limite, key):
    """
    Raccolta via API invece che via browser.

    Due motivi. In cloud non c'e' un browser credibile: da un IP di datacenter la Ad
    Library pubblica risponde con meno di quello che ha. E l'API impagina davvero, con
    un cursore, mentre la pagina pubblica si ferma intorno alle 30 per inserzionista:
    di Luiss ne dichiara 164 e il browser ce ne dava 30.
    """
    righe, visti, cursore, totale = [], set(), None, None
    while len(righe) < limite:
        q = f"company/ads?pageId={comp['page_id']}&country={paese}&status=ACTIVE"
        if cursore:
            q += "&cursor=" + quote(cursore)
        d = api_get(q, key)
        res = d.get("results") or []
        if totale is None:
            totale = d.get("searchResultsCount")
        nuovi = 0
        for n in res:
            aid = str(n.get("ad_archive_id") or "")
            if not aid or aid in visti:
                continue
            visti.add(aid)
            righe.append(normalize(n, comp, paese))
            nuovi += 1
        cursore = d.get("cursor")
        if not nuovi or not cursore:
            break
        time.sleep(0.3)
    return righe[:limite], totale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true", help="risolvi i page_id mancanti e esci")
    ap.add_argument("--only", action="append", default=[], help="slug da raccogliere (ripetibile)")
    ap.add_argument("--mercato", help="filtra per mercato: IT, EU, US")
    ap.add_argument("--limit", type=int, default=150, help="max inserzioni per competitor")
    ap.add_argument("--country", default=None, help="forza il paese della query (default: dal mercato)")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--keyword", action="store_true",
                    help="raccogli per parola chiave (keywords.txt) invece che per pagina: "
                         "per chi ha competitor senza un marchio da inseguire")
    ap.add_argument("--per-parola", type=int, default=120,
                    help="quante inserzioni al massimo per parola chiave")
    ap.add_argument("--api", action="store_true",
                    help="raccogli via ScrapeCreators invece che col browser: obbligatorio "
                         "in cloud, e prende tutte le inserzioni invece delle prime 30")
    ap.add_argument("--finestra", type=int, default=60,
                    help="giorni di raccolte da sommare nel catalogo")
    a = ap.parse_args()

    cfg = load_competitors()
    comps = [c for c in cfg["competitors"] if c.get("attivo", True)]
    if a.only:
        comps = [c for c in comps if c["slug"] in a.only]
    if a.mercato:
        comps = [c for c in comps if c.get("mercato") == a.mercato]

    # il cliente entra nel radar come chiunque altro: senza il suo metro, i numeri
    # degli altri non dicono niente
    cli = cfg.get("cliente") or {}
    if cli.get("page_id") and not a.only:
        comps = [{"slug": "_cliente", "nome": cli.get("brand", "cliente"),
                  "page_id": cli["page_id"], "mercato": cli.get("mercato", "IT"),
                  "tier": "cliente", "tipo": "cliente", "attivo": True}] + comps

    os.makedirs(RAW, exist_ok=True)
    stamp = date.today().isoformat()

    if a.keyword:
        key = load_key()
        if not key:
            sys.exit("--keyword ha bisogno di SCRAPECREATORS_API_KEY")
        fp_kw = os.path.join(os.path.dirname(RAW), "..", "keywords.txt")
        fp_kw = os.path.normpath(fp_kw)
        if not os.path.exists(fp_kw):
            sys.exit(f"manca {fp_kw}: una parola chiave per riga")
        parole = [l.strip() for l in open(fp_kw) if l.strip() and not l.startswith("#")]
        paesi = (cfg.get("cliente") or {}).get("paesi") or ["IT"]
        for paese in paesi:
            print(f"\n>> parole chiave [{paese}]", flush=True)
            righe = raccogli_keyword(parole, paese, a.per_parola, key, cfg)
            if not righe:
                continue
            suff = f"-{paese.lower()}" if len(paesi) > 1 else ""
            fp = os.path.join(RAW, f"_kw{suff}-{stamp}.jsonl")
            with open(fp, "w") as f:
                for r in righe:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"   -> {len(righe)} inserzioni da "
                  f"{len({r['competitor'] for r in righe})} inserzionisti", flush=True)
        # il cliente si raccoglie comunque per pagina: e' l'unico metro che conta
        cli = cfg.get("cliente") or {}
        if cli.get("page_id"):
            comp = {"slug": "_cliente", "nome": cli.get("brand", "noi"),
                    "mercato": (cli.get("paesi") or ["IT"])[0], "tier": "cliente",
                    "tipo": "cliente", "page_id": cli["page_id"]}
            for paese in paesi:
                rows, total = raccogli_api(comp, paese, a.limit, key)
                for r in rows:
                    r["ads_attive_brand"] = total
                if rows:
                    suff = f"-{paese.lower()}" if len(paesi) > 1 else ""
                    with open(os.path.join(RAW, f"_cliente{suff}-{stamp}.jsonl"), "w") as f:
                        for r in rows:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    print(f">> {comp['nome']} [{paese}]: {len(rows)} inserzioni", flush=True)
        return costruisci_catalogo(a.finestra)

    if a.api:
        key = load_key()
        if not key:
            sys.exit("--api ha bisogno di SCRAPECREATORS_API_KEY")
        if a.resolve:
            sys.exit("--resolve va lanciato col browser, non con --api")
        for c in comps:
            if not c.get("page_id"):
                print(f"-- {c['nome']}: page_id mancante, salto", flush=True)
                continue
            for paese in (c.get("paesi") or [a.country or ("IT" if c.get("mercato") == "IT" else "ALL")]):
                print(f"\n>> {c['nome']} [{paese}] via api", flush=True)
                try:
                    rows, total = raccogli_api(c, paese, a.limit, key)
                except Exception as e:
                    print(f"   ! {type(e).__name__}: {str(e)[:90]}", flush=True)
                    continue
                if not rows:
                    print("   nessuna inserzione attiva", flush=True)
                    continue
                for r in rows:
                    r["ads_attive_brand"] = total
                suffisso = f"-{paese.lower()}" if len(c.get("paesi") or []) > 1 else ""
                fp = os.path.join(RAW, f"{c['slug']}{suffisso}-{stamp}.jsonl")
                with open(fp, "w") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                print(f"   -> {len(rows)} inserzioni"
                      f"{f' (Meta ne dichiara {total})' if total else ''}", flush=True)
        return costruisci_catalogo(a.finestra)

    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        b, page = launch(p, a.headful)

        if a.resolve:
            cambi = 0
            for c in cfg["competitors"]:
                if c.get("page_id") or not c.get("attivo", True):
                    continue
                paese = "IT" if c.get("mercato") == "IT" else "ALL"
                print(f">> risolvo {c['nome']} ({paese})", flush=True)
                try:
                    hit = resolve_one(page, c["nome"], paese)
                except Exception as e:
                    print(f"   ! {type(e).__name__}", flush=True)
                    hit = None
                if hit:
                    c["page_id"] = hit["page_id"]
                    c["nota"] = (c.get("nota") or "").strip()
                    print(f"   -> {hit['page_id']}  ({hit['nome_pagina']}, {hit['ads_viste']} ads viste)", flush=True)
                    cambi += 1
                else:
                    print("   -> non trovato: mettilo a mano dalla Ad Library", flush=True)
                pause(3.0, 4.5)
            save_competitors(cfg)
            b.close()
            print(f"\n{cambi} page_id risolti in competitors.json")
            return

        raccolti = {}
        for c in comps:
            if not c.get("page_id"):
                print(f"-- {c['nome']}: page_id mancante, salto (lancia --resolve)", flush=True)
                continue
            paese = a.country or ("IT" if c.get("mercato") == "IT" else "ALL")
            print(f"\n>> {c['nome']} [{paese}]", flush=True)
            try:
                page.goto(page_url(c["page_id"], paese), timeout=60000,
                          wait_until="domcontentloaded")
            except Exception as e:
                print(f"   ! goto: {type(e).__name__}", flush=True)
                continue
            pause(3.5, 5.0)
            try:
                rows, total = harvest(page, c, paese, target=a.limit)
            except Exception as e:
                print(f"   ! harvest: {type(e).__name__}: {str(e)[:90]}", flush=True)
                continue
            if not rows:
                print("   nessuna inserzione attiva", flush=True)
                continue
            for r in rows:
                # il denominatore: quante inserzioni attive ha in totale quel brand
                r["ads_attive_brand"] = total
            fp = os.path.join(RAW, f"{c['slug']}-{stamp}.jsonl")
            with open(fp, "w") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            raccolti[c["slug"]] = fp
            print(f"   -> {len(rows)} inserzioni"
                  f"{f' (Meta ne dichiara {total})' if total else ''}", flush=True)
            pause(3.0, 4.5)
        b.close()

    costruisci_catalogo(a.finestra)


def costruisci_catalogo(finestra_giorni=60):
    """
    Il catalogo e' l'unione delle raccolte recenti, non solo l'ultima.

    La Ad Library, per ogni inserzionista, ne mostra circa 30 nell'ordine che sceglie lei
    (non per data: sui brand grossi sono le piu' longeve). Una raccolta sola e' quindi un
    campione, non il censimento. Sommando le raccolte, la copertura cresce settimana dopo
    settimana, e si vede anche cosa hanno spento: un concetto che ha girato 120 giorni e
    poi e' sparito dice quanto quello che e' ancora in aria.
    """
    from collections import defaultdict
    cfg = load_competitors()
    # comanda competitors.json: uno spento sparisce dalla dashboard, ma il suo storico
    # in data/raw/ resta dov'e' e torna se lo si riaccende
    ammessi = {c["slug"] for c in cfg["competitors"] if c.get("attivo", True)} | {"_cliente"}
    limite = (date.today() - timedelta(days=finestra_giorni)).isoformat()
    per_slug = defaultdict(list)
    esclusi = set()
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".jsonl"):
            continue
        # <slug>-AAAA-MM-GG.jsonl — lo slug puo' contenere cifre e trattini, quindi la
        # data si stacca dal fondo con un'espressione, non cercando un "-2" qualsiasi
        m = re.match(r"^(.+)-(\d{4}-\d{2}-\d{2})$", f[:-6])
        if not m:
            continue
        slug, giorno = m.group(1), m.group(2)
        # con piu' paesi il file e' <slug>-<paese>: il permesso si controlla sullo slug base
        base = slug.rsplit("-", 1)[0] if slug.rsplit("-", 1)[0] in ammessi else slug
        if slug.startswith("_kw"):
            base = "_kw"                      # raccolta per parola: nessuna lista da rispettare
            ammessi.add("_kw")
        if base not in ammessi:
            esclusi.add(slug)
            continue
        if giorno >= limite:
            per_slug[slug].append((giorno, os.path.join(RAW, f)))
    if esclusi:
        print(f"  esclusi perche' spenti in competitors.json: {', '.join(sorted(esclusi))}")

    n = 0
    with open(CATALOG, "w") as out:
        for slug, files in sorted(per_slug.items()):
            files.sort()
            ultimo_giro = files[-1][0]
            visti = {}
            for giorno, fp in files:                       # dal piu' vecchio al piu' nuovo
                for line in open(fp):
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    r["ultimo_avvistamento"] = giorno
                    r["primo_avvistamento"] = (visti.get(r["ad_archive_id"], {})
                                               .get("primo_avvistamento") or giorno)
                    visti[r["ad_archive_id"]] = r          # il record piu' fresco vince
            for r in visti.values():
                r["attiva_ora"] = r["ultimo_avvistamento"] == ultimo_giro
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                n += 1
    attive = sum(1 for l in open(CATALOG) if json.loads(l)["attiva_ora"])
    print(f"\ncatalogo: {n} inserzioni da {len(per_slug)} inserzionisti "
          f"({attive} ancora in aria) -> data/catalog.jsonl")


if __name__ == "__main__":
    main()
