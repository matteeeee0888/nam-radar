#!/usr/bin/env python3
"""
tag_ads.py — rigenera i dati della pagina Top Ads TAG, in sola lettura.

Legge il token da TAG_META_TOKEN (ads_read + read_insights) e scrive
$RADAR_SITE/ads/data.json più i file in ads/.media/. Non pubblica niente di
questo: tools/protect.py incorpora i dati, cifra pagina e media, e cancella
gli originali. Gli URL firmati di Meta non finiscono nel sito: le immagini e
i video restano file nostri. Non stampa spese né lead.

    TAG_META_TOKEN=... TAG_ADS_PASSWORD=... RADAR_SITE=... python tools/tag_ads.py
"""
import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

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
LATO_PIENO, LATO_MINI = 1400, 96
BUDGET_IMMAGINI, BUDGET_VIDEO = 120_000_000, 80_000_000
T = ""


def redact(testo):
    testo = str(testo)
    if T:
        testo = testo.replace(T, "***").replace(urllib.parse.quote(T, safe=""), "***")
    testo = re.sub(r"(access_token=)[^&\s\"']+", r"\1***", testo)
    return re.sub(r"https://[^\s\"'<>]+", "[url]", testo)


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
    """Creative in sola lettura, a blocchi di 50 ad id. Gli URL restano in memoria."""
    trovati = {}
    campi = "id,creative{thumbnail_url,image_url,image_hash,video_id,object_story_spec,asset_feed_spec}"
    for i in range(0, len(ids), 50):
        params = {
            "fields": campi,
            "filtering": json.dumps([{"field": "id", "operator": "IN", "value": ids[i:i + 50]}]),
        }
        try:
            righe = get(ACC + "/ads", params, 50)
        except RuntimeError:
            continue
        for v in righe:
            trovati[v["id"]] = estrai_asset(v.get("creative") or {})
    return trovati


def _aggiungi(lista, valore):
    if isinstance(valore, str) and valore.startswith("https://") and valore not in lista:
        lista.append(valore)


def _aggiungi_id(lista, valore):
    if valore is None or valore == "":
        return
    testo = str(valore)
    if testo.isdigit() and testo not in lista:
        lista.append(testo)


def estrai_asset(creative):
    """URL e id video da un creative. Non sceglie cosa tenere sul disco."""
    immagini, video, hash_img, miniature = [], [], [], []
    _aggiungi(miniature, creative.get("thumbnail_url"))
    _aggiungi(immagini, creative.get("image_url"))
    if isinstance(creative.get("image_hash"), str):
        hash_img.append(creative["image_hash"])
    _aggiungi_id(video, creative.get("video_id"))
    spec = creative.get("object_story_spec") or {}
    vd = spec.get("video_data") or {}
    _aggiungi_id(video, vd.get("video_id"))
    _aggiungi(immagini, vd.get("image_url"))
    ld = spec.get("link_data") or {}
    _aggiungi(immagini, ld.get("picture"))
    _aggiungi(immagini, ld.get("image_url"))
    if isinstance(ld.get("image_hash"), str):
        hash_img.append(ld["image_hash"])
    for child in (ld.get("child_attachments") or [])[:8]:
        _aggiungi(immagini, child.get("picture") or child.get("image_url"))
        _aggiungi_id(video, child.get("video_id"))
        if isinstance(child.get("image_hash"), str):
            hash_img.append(child["image_hash"])
    photo = spec.get("photo_data") or {}
    _aggiungi(immagini, photo.get("url") or photo.get("image_url"))
    feed = creative.get("asset_feed_spec") or {}
    for im in (feed.get("images") or [])[:8]:
        _aggiungi(immagini, im.get("url") or im.get("image_url"))
        if isinstance(im.get("hash") or im.get("image_hash"), str):
            hash_img.append(im.get("hash") or im["image_hash"])
    for voce in (feed.get("videos") or [])[:4]:
        _aggiungi_id(video, voce.get("video_id"))
        _aggiungi(immagini, voce.get("thumbnail_url"))
    return {"immagini": immagini, "video": video, "hash": list(dict.fromkeys(hash_img)), "miniature": miniature}


def graph_batch(urls):
    payload = urllib.parse.urlencode({
        "access_token": T,
        "include_headers": "false",
        "batch": json.dumps([{"method": "GET", "relative_url": u} for u in urls]),
    }).encode()
    richiesta = urllib.request.Request(API, data=payload)
    try:
        with urllib.request.urlopen(richiesta, timeout=120) as risposta:
            data = json.load(risposta)
    except Exception as e:
        print("batch", redact(e)[:160], file=sys.stderr)
        return [None] * len(urls)
    if not isinstance(data, list):
        print("batch rifiutato", redact(str(data)[:200]), file=sys.stderr)
        return [None] * len(urls)
    return data


def risolvi_hash(hashes):
    trovate = {}
    lista = list(dict.fromkeys(hashes))
    for i in range(0, len(lista), 50):
        params = {"hashes": json.dumps(lista[i:i + 50]), "fields": "hash,url"}
        try:
            righe = get(ACC + "/adimages", params, 50)
        except RuntimeError:
            continue
        for r in righe:
            if isinstance(r, dict) and str(r.get("url", "")).startswith("https://"):
                trovate[r.get("hash")] = r["url"]
    return trovate


def info_video(ids):
    info = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        risultati = graph_batch([f"{vid}?fields=source,picture,length" for vid in chunk])
        for vid, item in zip(chunk, risultati):
            if not item or item.get("code") != 200:
                continue
            try:
                body = json.loads(item.get("body") or "{}")
            except json.JSONDecodeError:
                continue
            info[vid] = body
        time.sleep(0.2)
    return info


def scarica_bytes(url, limite):
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    richiesta = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(richiesta, timeout=90) as risposta:
            buf = bytearray()
            while len(buf) <= limite:
                pezzo = risposta.read(256 * 1024)
                if not pezzo:
                    break
                buf += pezzo
            if len(buf) > limite or not buf:
                return None
            return bytes(buf)
    except Exception as e:
        print("download", redact(e)[:120], file=sys.stderr)
        return None


def _rgb(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        from PIL import Image
        rgba = im.convert("RGBA")
        fondo = Image.new("RGB", rgba.size, (255, 255, 255))
        fondo.paste(rgba, mask=rgba.split()[-1])
        return fondo
    return im.convert("RGB")


def webp_da_bytes(blob, lato, qualita):
    from PIL import Image
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    im = _rgb(Image.open(io.BytesIO(blob)))
    im.thumbnail((lato, lato), resample)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=qualita, method=4)
    return buf.getvalue()


def lavoro_immagine(url):
    blob = scarica_bytes(url, 12_000_000)
    if not blob:
        return url, None
    try:
        return url, (webp_da_bytes(blob, LATO_PIENO, 72), webp_da_bytes(blob, LATO_MINI, 52))
    except Exception:
        return url, None


def comprimi_video(sorgente, destinazione):
    base = ["ffmpeg", "-y", "-v", "error", "-i", sorgente,
            "-vf", "scale='min(720,iw)':-2:flags=lanczos",
            "-c:v", "libx264", "-crf", "32", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", "90", destinazione]
    con_audio = base[:-1] + ["-c:a", "aac", "-b:a", "64k", "-ac", "1", destinazione]
    try:
        if subprocess.run(con_audio, timeout=180).returncode == 0 and os.path.isfile(destinazione) and os.path.getsize(destinazione) > 1000:
            return True
        if os.path.exists(destinazione):
            os.remove(destinazione)
        return subprocess.run(base, timeout=180).returncode == 0 and os.path.isfile(destinazione) and os.path.getsize(destinazione) > 1000
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def fotogramma(video, destinazione):
    return subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "0.1", "-i", video, "-frames:v", "1", destinazione],
        timeout=60).returncode == 0 and os.path.exists(destinazione)


def _nome(blob, ext):
    return hashlib.sha256(blob).hexdigest()[:20] + ext


def _scrivi(cartella, nome, blob):
    path = os.path.join(cartella, nome)
    if not os.path.exists(path):
        tmp = path + ".parziale"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
    return os.path.getsize(path)


def salva_media(meta, spesa, cartella):
    """Scarica immagini e video e restituisce solo riferimenti locali."""
    os.makedirs(cartella, exist_ok=True)
    hashes = [h for asset in meta.values() for h in asset["hash"]]
    by_hash = risolvi_hash(hashes) if hashes else {}
    video_ids = list(dict.fromkeys(v for asset in meta.values() for v in asset["video"]))
    priorita = {}
    for ad_id, asset in meta.items():
        for vid in asset["video"]:
            priorita[vid] = max(priorita.get(vid, 0), spesa.get(ad_id, 0))
    video_ids.sort(key=lambda v: (-priorita.get(v, 0), v))
    video_ids = video_ids[:250]
    dettagli = info_video(video_ids) if video_ids else {}

    url_immagini = []
    for asset in meta.values():
        for url in asset["immagini"]:
            if url not in url_immagini:
                url_immagini.append(url)
        for h in asset["hash"]:
            url = by_hash.get(h)
            if url and url not in url_immagini:
                url_immagini.append(url)
        if not asset["immagini"] and not asset["hash"]:
            for url in asset["miniature"]:
                if url not in url_immagini:
                    url_immagini.append(url)
    for body in dettagli.values():
        pic = body.get("picture")
        if isinstance(pic, str) and pic.startswith("https://") and pic not in url_immagini:
            url_immagini.append(pic)

    pronti = {}
    for i in range(0, len(url_immagini), 16):
        with ThreadPoolExecutor(max_workers=8) as ex:
            for url, result in ex.map(lavoro_immagine, url_immagini[i:i + 16]):
                if result:
                    pronti[url] = result

    thumbs, creatives = {}, {}
    peso_img = peso_vid = 0
    n_img = n_vid = saltati = 0
    video_pronti = {}
    for vid in video_ids:
        body = dettagli.get(vid) or {}
        if body.get("length") and float(body["length"]) > 180:
            continue
        source = body.get("source")
        if not isinstance(source, str):
            continue
        grezzo = scarica_bytes(source, 45_000_000)
        if not grezzo:
            continue
        raw_path = os.path.join(cartella, vid + ".raw.mp4")
        out_path = os.path.join(cartella, vid + ".mp4")
        poster = os.path.join(cartella, vid + ".png")
        try:
            with open(raw_path, "wb") as f:
                f.write(grezzo)
            if not comprimi_video(raw_path, out_path):
                continue
            dimensione = os.path.getsize(out_path)
            if peso_vid + dimensione > BUDGET_VIDEO:
                saltati += 1
                continue
            peso_vid += dimensione
            mp4 = open(out_path, "rb").read()
            nome = _nome(mp4, ".mp4")
            _scrivi(cartella, nome, mp4)
            pronto = {"frame": {"kind": "video", "file": ".media/" + nome, "mime": "video/mp4"}}
            if fotogramma(os.path.join(cartella, nome), poster):
                try:
                    pronto["mini"] = webp_da_bytes(open(poster, "rb").read(), LATO_MINI, 52)
                except Exception:
                    pass
            video_pronti[vid] = pronto
            n_vid += 1
        finally:
            for tmp in (raw_path, out_path, poster):
                if os.path.exists(tmp):
                    os.remove(tmp)

    for ad_id, asset in meta.items():
        urls = list(asset["immagini"])
        for h in asset["hash"]:
            if by_hash.get(h) and by_hash[h] not in urls:
                urls.append(by_hash[h])
        if not urls:
            urls = list(asset["miniature"])
        frames, thumb_key = [], None
        for url in urls:
            coppia = pronti.get(url)
            if not coppia:
                continue
            pieno, mini = coppia
            if thumb_key is None:
                thumb_key = hashlib.sha256(mini).hexdigest()[:16]
                thumbs[thumb_key] = "data:image/webp;base64," + base64.b64encode(mini).decode("ascii")
            nome = _nome(pieno, ".webp")
            if peso_img + len(pieno) <= BUDGET_IMMAGINI or os.path.exists(os.path.join(cartella, nome)):
                peso_img += _scrivi(cartella, nome, pieno) if not os.path.exists(os.path.join(cartella, nome)) else 0
                if not any(f["file"] == ".media/" + nome for f in frames):
                    frames.append({"kind": "image", "file": ".media/" + nome, "mime": "image/webp"})
                    n_img += 1
            else:
                saltati += 1
        for vid in asset["video"]:
            pronto = video_pronti.get(vid)
            if not pronto:
                continue
            if not any(f["file"] == pronto["frame"]["file"] for f in frames):
                frames.insert(0, dict(pronto["frame"]))
            if thumb_key is None and pronto.get("mini"):
                thumb_key = hashlib.sha256(pronto["mini"]).hexdigest()[:16]
                thumbs[thumb_key] = "data:image/webp;base64," + base64.b64encode(pronto["mini"]).decode("ascii")
        voce = {}
        if thumb_key:
            voce["th"] = thumb_key
        if frames:
            voce["frames"] = frames[:12]
        if voce:
            creatives[ad_id] = voce
    print(f"media: {n_img} immagini, {n_vid} video, {len(thumbs)} miniature, {saltati} fuori budget")
    attesi = len(url_immagini) + len([1 for b in dettagli.values() if b.get("source")])
    if attesi and not thumbs and n_vid == 0:
        sys.exit("nessun media salvato, lascio la pagina precedente")
    return creatives, {"thumbs": thumbs}


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

    spesa = {a["id"]: a["spend"] for a in out["windows"].get("180", {}).get("ads", [])}
    meta = creatives(sorted(ids))
    if ids and not meta:
        sys.exit("creative non lette, lascio la pagina precedente")
    dest = os.path.join(sito(), "ads")
    out["creatives"], out["media"] = salva_media(meta, spesa, os.path.join(dest, ".media")) if meta else ({}, {"thumbs": {}})
    blob_media = json.dumps({"creatives": out["creatives"], "media": out["media"]}, ensure_ascii=False)
    if "https://" in blob_media or "http://" in blob_media or "access_token" in blob_media or (T and T in blob_media):
        sys.exit("i media contengono URL o il token, non li scrivo")
    testo = json.dumps(out, ensure_ascii=False)
    if (T and T in testo) or "access_token" in testo:
        sys.exit("il token è finito nei dati, non li scrivo")
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
