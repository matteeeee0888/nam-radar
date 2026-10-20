#!/usr/bin/env python3
"""
tag_ads.py — rigenera i dati della pagina Top Ads TAG, in sola lettura.

Legge TAG_META_TOKEN (permessi ads_read e read_insights) e interroga l'account
act_1943610831146. Scrive $RADAR_SITE/ads/data.json. Il file e' un passaggio
intermedio: tools/protect.py lo mette dentro la pagina e lo cancella, cosi' nel
repo resta solo ads/index.html cifrato.

    python tools/tag_ads.py
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("TAG_META_TOKEN", "")
ACC = "act_1943610831146"
BASE = "https://graph.facebook.com/v21.0/"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.environ.get("RADAR_SITE") or os.path.join(ROOT, "site")
FIELDS = ("campaign_id,campaign_name,adset_name,ad_name,ad_id,spend,"
          "impressions,clicks,inline_link_clicks,actions")

COURSES = {
    "NC": "Master in No Code AI", "AD": "Corso intensivo Design",
    "AL": "Corso intensivo in AI",
    "AM": "Master in Artificial Intelligence per il Marketing",
    "AP": "Master in AI Management", "AR": "Master in AI for Architecture",
    "AT": "Master in AI Augmented HR", "AV": "Master in AI for Advertising",
    "BR": "Breccia", "BV": "Master in Branding Visivo",
    "CC": "Corso intensivo in Cloud", "CM": "Master in Content Marketing",
    "DA": "Master in AI Powered Business", "DD": "Evento di posizionamento",
    "DM": "Master in Digital Marketing", "DR": "Digital Restart",
    "EC": "Master in AI for Ecommerce", "EM": "Executive MBA",
    "FG": "Figma from Zero to Hero", "FM": "Food Innovation Business",
    "GA": "Master in Generative AI", "HA": "Corso intensivo in HR",
    "HI": "Corso intensivo in AI per HR", "HP": "Master in Risorse Umane",
    "JH": "HR Payroll Specialist", "PR": "HR Payroll Specialist",
    "PD": "Master in Digital Product", "PK": "Master in Product Marketing",
    "UI": "Master in UI Design", "UX": "UX/UI Design",
    "VC": "Master in AI Engineering",
}
NONCORSO = re.compile(r"lead ?magnet|leadmagnet|evento|event|webinar|open ?day|masterclass", re.I)


def redact(text):
    out = re.sub(r"(access_token=)[^&\s\"']+", r"\1***", str(text))
    if TOKEN and TOKEN in out:
        out = out.replace(TOKEN, "***")
    return out


def call(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            msg = e.read()[:200].decode("utf-8", "ignore")
            print("HTTP", e.code, redact(msg), file=sys.stderr)
            # un 400 ripetuto non si sblocca ritentando la stessa query
            if e.code == 400 and i >= 1:
                return None
        except Exception as e:
            print("err", redact(str(e)[:200]), file=sys.stderr)
        time.sleep(5 * (i + 1))
    return None


def get(path, params, limit=100):
    params = dict(params, access_token=TOKEN, limit=limit)
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    out = []
    while url:
        data = call(url)
        if data is None:
            raise RuntimeError("page failed")
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
    # pagine troppo grandi tornano 400: si riprova con pagine piu' corte
    for limit in (100, 50, 25):
        try:
            return get(obj + "/insights", params, limit)
        except RuntimeError:
            pass
    raise RuntimeError(f"insights failed {obj} {since}")


def ad_level(since, until):
    """Insight a livello ad, spezzati per campagna per evitare i 400 di paginazione."""
    camps = insights(ACC, since, until, "campaign")
    rows = []
    for camp in camps:
        rows += insights(camp["campaign_id"], since, until, "ad")
    return camps, rows


def action(row, kind):
    return sum(float(a["value"]) for a in row.get("actions", []) if a["action_type"] == kind)


def classify(name):
    match = re.match(r"^IT([A-Z]{2})([A-Z]{2})(?![A-Za-z])", name or "")
    if not match:
        return "NC", "Non-corso", ""
    if match.group(1) + match.group(2) == "EMBA":
        return "EMBA", "Executive MBA", "BA"
    return match.group(1), COURSES.get(match.group(1), match.group(1)), match.group(2)


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
    """Thumbnail e link di anteprima, a blocchi di 50 ad id. Sola lettura."""
    found = {}
    for i in range(0, len(ids), 50):
        params = {
            "fields": "id,preview_shareable_link,creative{thumbnail_url,image_url}",
            "filtering": json.dumps([{"field": "id", "operator": "IN", "value": ids[i:i + 50]}]),
        }
        try:
            rows = get(ACC + "/ads", params, 50)
        except RuntimeError:
            continue
        for row in rows:
            creative = row.get("creative", {})
            found[row["id"]] = {
                "th": creative.get("thumbnail_url") or creative.get("image_url"),
                "pv": row.get("preview_shareable_link"),
            }
    return found


def main():
    if not TOKEN:
        sys.exit("manca TAG_META_TOKEN")
    today = dt.date.today()
    until = (today - dt.timedelta(days=1)).isoformat()
    windows = {"30": 30, "90": 91, "180": 182}
    out = {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "until": until,
        "windows": {},
    }
    ids = set()
    for key, days in windows.items():
        since = (today - dt.timedelta(days=days)).isoformat()
        camps, rows = ad_level(since, until)
        ads = []
        for row in rows:
            source = row["ad_name"] if re.match(r"^IT[A-Z]{4}", row["ad_name"] or "") else row["campaign_name"]
            code, course, cf = classify(source)
            if NONCORSO.search((row["campaign_name"] or "") + " " + (row["ad_name"] or "")):
                code, course = "NC", "Non-corso"
            ads.append({
                "id": row["ad_id"],
                "ad": row["ad_name"],
                "camp": row["campaign_name"],
                "adset": row["adset_name"],
                "course": course,
                "cf": cf,
                "fmt": formato(row["ad_name"]),
                "spend": round(float(row["spend"]), 2),
                "impr": int(row["impressions"]),
                "clicks": int(row.get("clicks", 0)),
                "link": int(row.get("inline_link_clicks", 0) or 0),
                "lead": int(action(row, "lead")),
            })
            ids.add(row["ad_id"])
        out["windows"][key] = {"since": since, "ads": ads, "campaigns": len(camps)}
        print(key, since, len(camps), "campagne", len(ads), "ads",
              round(sum(a["spend"] for a in ads)), "EUR",
              sum(a["lead"] for a in ads), "lead")
    out["creatives"] = creatives(sorted(ids))
    dest_dir = os.path.join(SITE, "ads")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "data.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"scritto {dest}")


if __name__ == "__main__":
    main()
