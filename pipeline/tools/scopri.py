#!/usr/bin/env python3
"""
scopri.py — chi sta pagando su queste parole, anche se non sai come si chiama.

Serve quando i competitor non hanno un marchio da cercare: un installatore di caldaie,
un e-commerce di cibo per cani nato l'anno scorso, un tour operator locale. Invece di
partire dai nomi si parte dalla domanda: si interroga la Ad Library per parola chiave e
si guarda chi compra quelle parole, ordinato per quante inserzioni ci tiene sopra.

    ~/tools/Scrapling/.venv/bin/python tools/scopri.py parole.txt --paese IT
    ... --pagine 4 --json scoperti.json

Il risultato non e' un set di competitor: e' una lista da leggere. Decidi tu chi entra.
"""
import argparse, json, os, sys, time
from collections import defaultdict
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adlib import api_get, slugify
from enrich_reach import load_key


def cerca(parola, paese, pagine, key):
    """Una parola, piu' pagine di risultati. Ogni pagina e' una chiamata."""
    trovati, cursore = [], None
    for _ in range(pagine):
        q = (f"search/ads?query={quote(parola)}&country={paese}"
             f"&status=ACTIVE&media_type=ALL&trim=true")
        if cursore:
            q += "&cursor=" + quote(cursore)
        d = None
        for tentativo in range(3):          # la rete cade, e perdere una parola falsa il quadro
            try:
                d = api_get(q, key)
                break
            except Exception as e:
                if tentativo == 2:
                    print(f"   ! {parola}: {type(e).__name__} dopo 3 tentativi", flush=True)
                else:
                    time.sleep(2 + tentativo * 3)
        if d is None:
            break
        # la ricerca per parola risponde in searchResults, la ricerca per pagina in results
        res = d.get("searchResults") or d.get("results") or []
        trovati += res
        cursore = d.get("cursor")
        if not res or not cursore:
            break
        time.sleep(0.3)
    return trovati


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="una parola chiave per riga")
    ap.add_argument("--paese", default="IT")
    ap.add_argument("--pagine", type=int, default=3, help="pagine per parola (1 credito l'una)")
    ap.add_argument("--min", type=int, default=2, help="soglia sotto cui non si stampa")
    ap.add_argument("--json")
    a = ap.parse_args()
    key = load_key()
    if not key:
        sys.exit("SCRAPECREATORS_API_KEY non trovata")

    parole = [l.strip() for l in open(a.file) if l.strip() and not l.startswith("#")]
    per_pagina = defaultdict(lambda: {"nome": None, "ads": set(), "parole": set()})

    for parola in parole:
        res = cerca(parola, a.paese, a.pagine, key)
        print(f"  {parola:38} {len(res):>4} inserzioni", flush=True)
        for n in res:
            snap = n.get("snapshot") or {}
            pid = str(n.get("page_id") or snap.get("page_id") or "")
            if not pid:
                continue
            v = per_pagina[pid]
            v["nome"] = v["nome"] or snap.get("page_name") or n.get("page_name")
            v["ads"].add(str(n.get("ad_archive_id")))
            v["parole"].add(parola)

    righe = sorted(
        ({"page_id": k, "nome": v["nome"], "ads": len(v["ads"]),
          "parole": sorted(v["parole"])} for k, v in per_pagina.items()),
        key=lambda r: -r["ads"])
    righe = [r for r in righe if r["ads"] >= a.min]

    print(f"\n{'inserzionista':40} {'ads':>4}  su quali parole")
    print("-" * 100)
    for r in righe[:45]:
        print(f"{(r['nome'] or '?')[:39]:40} {r['ads']:>4}  {', '.join(r['parole'])[:50]}")
    if a.json:
        json.dump(righe, open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"\n-> {a.json}")
    print(f"\n{len(righe)} inserzionisti con almeno {a.min} inserzioni su queste parole")


if __name__ == "__main__":
    main()
