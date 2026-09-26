#!/usr/bin/env python3
"""
enrich_reach.py — aggiunge alle inserzioni la reach UE dichiarata da Meta.

Perche' serve. Negli Stati Uniti la Ad Library non pubblica nessun numero: li' si ordina
per indizi (da quanto e' in aria, quante varianti, che quota del portafoglio occupa).
In Europa il DSA obbliga Meta a pubblicare, per ogni inserzione servita nell'UE, le
persone raggiunte e la scomposizione per paese, eta' e genere. Per un cliente che fa
mercato italiano questo cambia tutto: non e' un proxy, e' il dato.

Sta sulla scheda di dettaglio dell'inserzione, dietro una chiamata separata che la
pagina pubblica non espone nel payload. Lo prendiamo via ScrapeCreators (l'account che
NAM usa gia'): 1 credito per inserzione, e con la cache 0 crediti se l'hai gia' chiesta
di recente.

Prerequisito, una volta sola: metti la chiave nel .env del workspace
    SCRAPECREATORS_API_KEY=...          (da scrapecreators.com, sezione API key)

    PY=~/tools/Scrapling/.venv/bin/python
    $PY tools/enrich_reach.py --check        # prova su una sola inserzione
    $PY tools/enrich_reach.py                # arricchisce tutto il catalogo
    $PY tools/enrich_reach.py --max 200      # tetto di spesa: 200 crediti
"""
import argparse, json, os, sys, time
from datetime import date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adlib import CATALOG, DATA

API = "https://api.scrapecreators.com/v1/facebook/adLibrary/ad"
CACHE = os.path.join(DATA, "reach_cache.json")


def load_key():
    key = os.environ.get("SCRAPECREATORS_API_KEY")
    if key:
        return key
    here = os.path.abspath(__file__)
    # il .env sta al root del workspace: si risale finche' non lo si trova
    d = os.path.dirname(here)
    for _ in range(8):
        fp = os.path.join(d, ".env")
        if os.path.exists(fp):
            for line in open(fp):
                line = line.strip()
                if line.startswith("SCRAPECREATORS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        d = os.path.dirname(d)
    return None


def fetch(ad_id, key, cache_days="7d"):
    url = f"{API}?id={ad_id}&cache_max_age={cache_days}"
    req = Request(url, headers={"x-api-key": key, "Accept": "application/json"})
    with urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def extract(payload):
    """La reach UE sta in eu_transparency (o nel suo alias aaa_info)."""
    eu = payload.get("eu_transparency") or payload.get("aaa_info") or {}
    if not eu:
        return None
    br = eu.get("age_country_gender_reach_breakdown") or []
    per_paese, per_eta, per_genere = {}, {}, {"uomini": 0, "donne": 0, "n.d.": 0}
    for paese in br:
        cc = paese.get("country")
        tot_paese = 0
        for b in paese.get("age_gender_breakdowns") or []:
            m = b.get("male") or 0
            f = b.get("female") or 0
            u = b.get("unknown") or 0
            tot_paese += m + f + u
            eta = b.get("age_range") or "n.d."
            per_eta[eta] = per_eta.get(eta, 0) + m + f + u
            per_genere["uomini"] += m
            per_genere["donne"] += f
            per_genere["n.d."] += u
        if cc:
            per_paese[cc] = per_paese.get(cc, 0) + tot_paese
    return {
        "eu_reach": eu.get("eu_total_reach"),
        "reach_per_paese": per_paese,
        "reach_per_eta": per_eta,
        "reach_per_genere": per_genere,
        "target_paesi": [l.get("name") for l in (eu.get("location_audience") or [])
                         if not l.get("excluded")],
        "target_genere": eu.get("gender_audience"),
        "target_eta": eu.get("age_audience"),
        "pagante": (eu.get("payer_beneficiary_data") or [{}])[0].get("payer"),
        "arricchito_il": date.today().isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="prova su una sola inserzione")
    ap.add_argument("--max", type=int, default=1500, help="tetto di crediti per questo giro")
    ap.add_argument("--cache-days", default="7d", choices=["1d", "3d", "7d", "14d", "30d"])
    ap.add_argument("--force", action="store_true", help="riscarica anche quelle gia' in cache")
    a = ap.parse_args()

    key = load_key()
    if not key:
        sys.exit("SCRAPECREATORS_API_KEY non trovata (ne' in ambiente ne' nel .env del workspace).\n"
                 "Senza chiave la dashboard funziona lo stesso: ordina per trazione invece che per reach.")
    if not os.path.exists(CATALOG):
        sys.exit("catalogo vuoto: lancia prima tools/fetch.py")

    rows = [json.loads(l) for l in open(CATALOG) if l.strip()]
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    todo = [r for r in rows if r["ad_archive_id"] and (a.force or r["ad_archive_id"] not in cache)]
    if a.check:
        todo = todo[:1]
    todo = todo[:a.max]
    print(f"{len(rows)} inserzioni in catalogo, {len(cache)} gia' in cache, {len(todo)} da chiedere")

    ok = ko = 0
    for i, r in enumerate(todo, 1):
        aid = r["ad_archive_id"]
        try:
            payload = fetch(aid, key, a.cache_days)
        except HTTPError as e:
            print(f"  [{i}/{len(todo)}] {aid}: HTTP {e.code}", flush=True)
            ko += 1
            if e.code in (401, 403):
                sys.exit("chiave rifiutata: controlla SCRAPECREATORS_API_KEY")
            if e.code == 402:
                sys.exit("crediti esauriti sull'account ScrapeCreators")
            time.sleep(1.0)
            continue
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  [{i}/{len(todo)}] {aid}: {type(e).__name__}", flush=True)
            ko += 1
            time.sleep(1.0)
            continue
        dati = extract(payload)
        if dati and dati.get("eu_reach") is not None:
            cache[aid] = dati
            ok += 1
            if a.check or i % 25 == 0:
                print(f"  [{i}/{len(todo)}] {r['competitor'][:28]:30} reach UE {dati['eu_reach']:,}".replace(",", "."), flush=True)
        else:
            # inserzione non servita nell'UE (o senza trasparenza): si segna, non si richiede
            cache[aid] = {"eu_reach": None, "arricchito_il": date.today().isoformat()}
            ko += 1
        time.sleep(0.25)
        if i % 50 == 0:
            json.dump(cache, open(CACHE, "w"), ensure_ascii=False)

    json.dump(cache, open(CACHE, "w"), ensure_ascii=False)
    print(f"\n{ok} con reach UE, {ko} senza. Cache: data/reach_cache.json")
    print("Ora: tools/build.py")


if __name__ == "__main__":
    main()
