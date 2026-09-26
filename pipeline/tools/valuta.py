#!/usr/bin/env python3
"""
valuta.py — dato un elenco di nomi, dice chi fa davvero pubblicita' su Meta in Italia.

Serve prima di allargare competitors.json. Un nome in una lista non e' un competitor
utile a questo radar: lo e' se ha una pagina Meta e sopra ci sono inserzioni attive in
Italia. Chi ne ha zero e' rumore, e sporca la classifica senza aggiungere niente.

    ~/tools/Scrapling/.venv/bin/python tools/valuta.py nomi.txt
    ~/tools/Scrapling/.venv/bin/python tools/valuta.py nomi.txt --json out.json
"""
import argparse, json, os, sys, time, unicodedata, re
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enrich_reach import load_key

BASE = "https://api.scrapecreators.com/v1/facebook/adLibrary"


def slug(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def chiama(url, key):
    req = Request(url, headers={"x-api-key": key, "Accept": "application/json"})
    with urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def cerca(nome, key):
    d = chiama(f"{BASE}/search/companies?query={quote(nome)}", key)
    return d.get("searchResults") or []


def ads_it(page_id, key, paese="IT"):
    d = chiama(f"{BASE}/company/ads?pageId={page_id}&country={paese}&status=ACTIVE&trim=true", key)
    res = d.get("results") or []
    return len(res), d.get("searchResultsCount"), bool(d.get("cursor"))


def scegli(nome, risultati):
    """Il candidato giusto e' quello il cui nome somiglia davvero, non il piu' popolare."""
    want = slug(nome)
    parole = [p for p in want.split() if len(p) > 2 and p not in
              ("srl", "spa", "s p a", "s r l", "group", "italia", "the")]
    segnati = []
    for r in risultati:
        n = slug(r.get("name"))
        if n == want:
            g = 4
        elif n.startswith(want) or want.startswith(n):
            g = 3
        elif parole and all(p in n for p in parole):
            g = 2
        elif parole and parole[0] in n:
            g = 1
        else:
            g = 0
        if g:
            segnati.append((g, r.get("likes") or 0, r))
    if not segnati:
        return None
    segnati.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return segnati[0][2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="un nome per riga")
    ap.add_argument("--json", help="dove scrivere l'esito completo")
    ap.add_argument("--paese", default="IT", help="codice paese di consegna (IT, ES, DE, GB...)")
    a = ap.parse_args()
    key = load_key()
    if not key:
        sys.exit("SCRAPECREATORS_API_KEY non trovata")

    nomi = [l.strip() for l in open(a.file) if l.strip() and not l.startswith("#")]
    esiti = []
    print(f"{'nome':42} {'pagina trovata':38} {'ads ' + a.paese:>7}  note")
    print("-" * 104)
    for nome in nomi:
        riga = {"nome": nome}
        try:
            cand = scegli(nome, cerca(nome, key))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"{nome[:41]:42} {'! ' + type(e).__name__:38} {'':>7}")
            riga["errore"] = type(e).__name__
            esiti.append(riga)
            continue
        if not cand:
            print(f"{nome[:41]:42} {'— nessuna pagina riconducibile':38} {'':>7}")
            riga["trovato"] = False
            esiti.append(riga)
            time.sleep(0.3)
            continue
        riga.update({"trovato": True, "page_id": cand["page_id"],
                     "nome_pagina": cand.get("name"), "likes": cand.get("likes"),
                     "categoria": cand.get("category")})
        try:
            n, tot, altre = ads_it(cand["page_id"], key, a.paese)
        except Exception as e:
            n, tot, altre = None, None, False
            riga["errore_ads"] = type(e).__name__
        riga.update({"ads_it": n, "ads_dichiarate": tot, "altre_pagine": altre})
        nota = "" if n else f"nessuna inserzione attiva in {a.paese}"
        if n and altre:
            nota = "ne ha altre oltre alle prime 30"
        print(f"{nome[:41]:42} {(cand.get('name') or '')[:37]:38} {str(n):>7}  {nota}")
        esiti.append(riga)
        time.sleep(0.3)

    if a.json:
        json.dump(esiti, open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"\n-> {a.json}")
    attivi = [e for e in esiti if e.get("ads_it")]
    print(f"\n{len(attivi)} su {len(nomi)} hanno inserzioni attive in {a.paese}")


if __name__ == "__main__":
    main()
