#!/usr/bin/env python3
"""
tag_ads.py — rigenera i dati della pagina Top Ads TAG, in sola lettura.

Legge il token da TAG_META_TOKEN (ads_read + read_insights) e scrive
$RADAR_SITE/ads/data.json. Non pubblica quel file: tools/protect.py lo
incorpora, cifra la pagina e lo cancella. Non stampa spese né lead.

    TAG_META_TOKEN=... TAG_ADS_PASSWORD=... RADAR_SITE=... python tools/tag_ads.py
"""
import datetime as dt
import io
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

ACC = "act_1943610831146"
API = "https://graph.facebook.com/v21.0/"
FIELDS = "campaign_id,campaign_name,adset_name,ad_name,ad_id,spend,impressions,clicks,inline_link_clicks,actions"
COURSES = {
    "NC": "Master in No Code AI",
    "AD": "Corso intensivo Design",
    "AL": "Corso intensivo in AI",
    "AM": "Master in Artificial Intelligence per il Marketing",
    "AP": "Master in AI Management",
    "AR": "Master in AI for Architecture",
    "AT": "Master in AI Augmented HR",
    "AV": "Master in AI for Advertising",
    "BR": "Breccia",
    "BV": "Master in Branding Visivo",
    "CC": "Corso intensivo in Cloud",
    "CM": "Master in Content Marketing",
    "DA": "Master in AI Powered Business",
    "DD": "Evento di posizionamento",
    "DM": "Master in Digital Marketing",
    "DR": "Digital Restart",
    "EC": "Master in AI for Ecommerce",
    "EM": "Executive MBA",
    "FG": "Figma from Zero to Hero",
    "FM": "Food Innovation Business",
    "GA": "Master in Generative AI",
    "HA": "Corso intensivo in HR",
    "HI": "Corso intensivo in AI per HR",
    "HP": "Master in Risorse Umane",
    "JH": "HR Payroll Specialist",
    "PR": "HR Payroll Specialist",
    "PD": "Master in Digital Product",
    "PK": "Master in Product Marketing",
    "UI": "Master in UI Design",
    "UX": "UX/UI Design",
    "VC": "Master in AI Engineering",
}
NONCORSO = re.compile(r"lead ?magnet|leadmagnet|evento|event|webinar|open ?day|masterclass", re.I)
T = ""


def redact(testo):
    testo = str(testo)
    if T:
        testo = testo.replace(T, "***").replace(urllib.parse.quote(T, safe=""), "***")
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", testo)


def sito():
    qui = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(qui))
    return os.environ.get("RADAR_SITE") or repo


def call(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as risposta:
                return json.load(risposta)
        except urllib.error.HTTPError as e:
            msg = redact(e.read()[:300].decode("utf-8", "ignore"))
            print("HTTP", e.code, msg, file=sys.stderr)
            if e.code in (401, 403) or "OAuthException" in msg or re.search(r'"code"\s*:\s*190', msg):
                sys.exit("Meta ha rifiutato il token")
            if e.code == 400 and i >= 1:
                return None
        except Exception as e:
            print("err", redact(e)[:120], file=sys.stderr)
        time.sleep(5 * (i + 1))
    return None


def get(path, params, limit=100):
    params = dict(params, access_token=T, limit=limit)
    url = API + path + "?" + urllib.parse.urlencode(params)
    out = []
    while url:
        data = call(url)
        if data is None:
            raise RuntimeError("pagina API fallita")
        out += data.get("data", [])
        url = data.get("paging", {}).get("next")
    return out


def insights(obj, since, until, level):
    params = {
        "level": level,
        "fields": FIELDS,
        "time_range": json.dumps({"since": since, "until": until}),
        "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}]),
    }
    for lim in (100, 50, 25):
        try:
            return get(obj + "/insights", params, lim)
        except RuntimeError:
            pass
    raise RuntimeError(f"insights falliti {since}")


def ad_level(since, until):
    """Ad-level aggregato, spezzato per campagna per evitare i 400 di paginazione."""
    campagne = insights(ACC, since, until, "campaign")
    righe = []
    for c in campagne:
        righe += insights(c["campaign_id"], since, until, "ad")
    return campagne, righe


def azione(riga, tipo):
    return sum(float(a["value"]) for a in riga.get("actions", []) if a["action_type"] == tipo)


def classify(name):
    m = re.match(r"^IT([A-Z]{2})([A-Z]{2})(?![A-Za-z])", name or "")
    if not m:
        return "NC", "Non-corso", ""
    if m.group(1) + m.group(2) == "EMBA":
        return "EMBA", "Executive MBA", "BA"
    return m.group(1), COURSES.get(m.group(1), m.group(1)), m.group(2)


def formato(name):
    n = (name or "").lower()
    if "carou" in n or "carosel" in n:
        return "Carosello"
    if "video" in n or "reel" in n or "ugc" in n:
        return "Video"
    if "static" in n or "statico" in n or "image" in n or "img" in n:
        return "Static"
    return "Altro"


def creatives(ids):
    """Thumbnail e link di anteprima, a blocchi di 50 ad id."""
    trovati = {}
    for i in range(0, len(ids), 50):
        params = {
            "fields": "id,preview_shareable_link,creative{thumbnail_url,image_url}",
            "filtering": json.dumps([{"field": "id", "operator": "IN", "value": ids[i:i + 50]}]),
        }
        try:
            righe = get(ACC + "/ads", params, 50)
        except RuntimeError:
            continue
        for v in righe:
            c = v.get("creative", {})
            trovati[v["id"]] = {
                "th": c.get("thumbnail_url") or c.get("image_url"),
                "pv": v.get("preview_shareable_link"),
            }
    return trovati


def main():
    global T
    T = os.environ.get("TAG_META_TOKEN", "").strip()
    if not T:
        sys.exit("manca il secret TAG_META_TOKEN")
    if not os.environ.get("TAG_ADS_PASSWORD", "").strip():
        sys.exit("manca il secret TAG_ADS_PASSWORD")

    oggi = dt.date.today()
    until = (oggi - dt.timedelta(days=1)).isoformat()
    finestre = {"30": 30, "90": 91, "180": 182}
    out = {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "until": until,
        "windows": {},
    }
    ids = set()
    for chiave, giorni in finestre.items():
        since = (oggi - dt.timedelta(days=giorni)).isoformat()
        campagne, righe = ad_level(since, until)
        ads = []
        for r in righe:
            nome = r["ad_name"] if re.match(r"^IT[A-Z]{4}", r["ad_name"] or "") else r["campaign_name"]
            code, course, ff = classify(nome)
            if NONCORSO.search((r.get("campaign_name") or "") + " " + (r.get("ad_name") or "")):
                code, course = "NC", "Non-corso"
            ads.append({
                "id": r["ad_id"],
                "ad": r["ad_name"],
                "camp": r["campaign_name"],
                "adset": r["adset_name"],
                "course": course,
                "cf": ff,
                "fmt": formato(r["ad_name"]),
                "spend": round(float(r["spend"]), 2),
                "impr": int(r["impressions"]),
                "clicks": int(r.get("clicks", 0)),
                "link": int(r.get("inline_link_clicks", 0) or 0),
                "lead": int(azione(r, "lead")),
            })
            ids.add(r["ad_id"])
        out["windows"][chiave] = {"since": since, "ads": ads, "campaigns": len(campagne)}
        print(chiave, since, len(campagne), "campagne", len(ads), "ads")

    out["creatives"] = creatives(sorted(ids))
    testo = json.dumps(out, ensure_ascii=False)
    if (T and T in testo) or "access_token" in testo:
        sys.exit("il token è finito nei dati, non li scrivo")
    dest = os.path.join(sito(), "ads")
    os.makedirs(dest, exist_ok=True)
    parziale = os.path.join(dest, ".data.json.partial")
    with open(parziale, "w", encoding="utf-8") as f:
        f.write(testo)
        f.write("\n")
    os.replace(parziale, os.path.join(dest, "data.json"))
    print("dati pronti per la cifratura")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        print(redact(buf.getvalue())[-2000:], file=sys.stderr)
        print("tag_ads:", redact(e)[:300], file=sys.stderr)
        sys.exit(1)
