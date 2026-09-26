#!/usr/bin/env python3
"""
fetch_videos.py — scarica i video delle inserzioni in docs/media/<ad_id>.mp4.

Le URL dei video della Ad Library scadono nel giro di giorni: vanno prese subito dopo
la raccolta, altrimenti nella dashboard resta solo la copertina. Il file locale invece
non scade piu'.

    python3 tools/fetch_videos.py
    python3 tools/fetch_videos.py --max 40      # tetto, se si vuole solo un assaggio
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adlib import CATALOG, ROOT

MEDIA = os.path.join(ROOT, "docs", "media")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"


def scarica(job):
    aid, url = job
    fp = os.path.join(MEDIA, f"{aid}.mp4")
    if os.path.exists(fp) and os.path.getsize(fp) > 20000:
        return ("gia", aid, os.path.getsize(fp))
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urlopen(req, timeout=90) as r:
            b = r.read()
        if len(b) < 20000:
            return ("vuoto", aid, len(b))
        open(fp, "wb").write(b)
        return ("ok", aid, len(b))
    except Exception as e:
        return (type(e).__name__, aid, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--salta-se-in", metavar="DIR",
                    help="non scaricare i video che risultano gia' ricompressi qui dentro "
                         "(in cloud evita di riscaricare 1 GB per niente)")
    a = ap.parse_args()
    os.makedirs(MEDIA, exist_ok=True)
    rows = [json.loads(l) for l in open(CATALOG) if l.strip()]
    gia = set()
    if a.salta_se_in and os.path.isdir(a.salta_se_in):
        gia = {f[:-4] for f in os.listdir(a.salta_se_in) if f.endswith(".mp4")}
        print(f"{len(gia)} gia' ricompressi: non si riscaricano")
    jobs, visti = [], set()
    for r in rows:
        u = (r.get("video_urls") or [None])[0]
        if u and r["ad_archive_id"] not in visti and r["ad_archive_id"] not in gia:
            visti.add(r["ad_archive_id"])
            jobs.append((r["ad_archive_id"], u))
    jobs = jobs[:a.max]
    print(f"{len(jobs)} video da prendere")
    tot = ok = gia = ko = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (esito, aid, n) in enumerate(ex.map(scarica, jobs), 1):
            tot += n
            if esito == "ok":
                ok += 1
            elif esito == "gia":
                gia += 1
            else:
                ko += 1
                print(f"  {aid}: {esito}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)} · {tot/1e6:.0f} MB", flush=True)
    print(f"\n{ok} scaricati, {gia} gia' presenti, {ko} falliti · {tot/1e6:.0f} MB in docs/media/")


if __name__ == "__main__":
    main()
