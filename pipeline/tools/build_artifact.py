#!/usr/bin/env python3
"""
build_artifact.py — impacchetta la dashboard per la pubblicazione come pagina condivisibile.

La versione in docs/ carica le anteprime da docs/media/: 339 file per 21 MB, che vanno
bene su disco ma non viaggiano. Qui le anteprime vengono ridotte, convertite in WebP e
messe tutte dentro un unico media.js come data URI, cosi' la pagina e' due file e si
apre uguale da qualsiasi parte.

    ~/tools/Scrapling/.venv/bin/python tools/build_artifact.py     # serve PIL
"""
import base64, io, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
MEDIA = os.path.join(DOCS, "media")
OUT = os.path.join(ROOT, "build")

LATO = 320          # le card mostrano circa 282px: oltre non si vede
QUALITA = 62

# La piattaforma accetta 64 MB per versione: si sta sotto con margine, e si decide
# in che ordine spendere lo spazio invece di lasciarlo decidere all'ordine dei file.
BUDGET_VIDEO = 52_000_000
VCRF, VLARGO, VFPS, VAUDIO = "37", "320", "24", "32k"


def comprimi(fp):
    from PIL import Image
    im = Image.open(fp).convert("RGB")
    w, h = im.size
    lato = min(w, h)                                  # ritaglio quadrato centrato
    im = im.crop(((w - lato) // 2, (h - lato) // 2,
                  (w + lato) // 2, (h + lato) // 2)).resize((LATO, LATO), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=QUALITA, method=6)
    return buf.getvalue()


def comprimi_video(sorgente, destinazione):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", sorgente,
         "-vf", f"scale='min({VLARGO},iw)':-2:flags=lanczos",
         "-c:v", "libx264", "-crf", VCRF, "-preset", "veryfast", "-r", VFPS,
         "-c:a", "aac", "-b:a", VAUDIO, "-ac", "1",
         "-movflags", "+faststart", destinazione],
        check=True)
    return os.path.getsize(destinazione)


def ordina_per_importanza(ads):
    """
    Prima un video per concetto, poi gli altri. Se lo spazio finisce, a restare fuori
    sono i doppioni dentro un concetto gia' rappresentato, non i concetti.
    """
    peso = lambda a: ((a.get("eu_reach") or 0) * 1e6 + (a.get("giorni_in_aria") or 0))
    capi, resto, visti = [], [], set()
    for a in sorted(ads, key=peso, reverse=True):
        (resto if a.get("concetto") in visti else capi).append(a)
        visti.add(a.get("concetto"))
    return capi + resto


def main():
    src = os.path.join(DOCS, "index.html")
    if not os.path.exists(src):
        sys.exit("manca docs/index.html: lancia prima tools/build.py")
    html = open(src).read()

    # quali anteprime servono davvero
    m = re.search(r"const D = (\{.*?\});\n", html, re.S)
    ads = json.loads(m.group(1))["ads"]
    ids = [a["ad_archive_id"] for a in ads]

    mappa, pesi = {}, 0
    for aid in ids:
        fp = os.path.join(MEDIA, f"{aid}.jpg")
        if not os.path.exists(fp):
            continue
        try:
            b = comprimi(fp)
        except Exception:
            continue
        mappa[aid] = "data:image/webp;base64," + base64.b64encode(b).decode()
        pesi += len(b)

    # --- video: si codifica finche' c'e' budget, nell'ordine che conta
    VOUT = os.path.join(OUT, "v")
    shutil.rmtree(VOUT, ignore_errors=True)
    os.makedirs(VOUT, exist_ok=True)
    video, speso, fuori = {}, 0, 0
    for a in ordina_per_importanza([x for x in ads if x.get("video_local")]):
        aid = a["ad_archive_id"]
        if aid in video:
            continue
        sorgente = os.path.join(DOCS, a["video_local"])
        if not os.path.exists(sorgente):
            continue
        dest = os.path.join(VOUT, f"{aid}.mp4")
        try:
            n = comprimi_video(sorgente, dest)
        except subprocess.CalledProcessError:
            continue
        if speso + n > BUDGET_VIDEO:
            os.remove(dest)
            fuori += 1
            continue
        speso += n
        video[aid] = f"v/{aid}.mp4"

    os.makedirs(OUT, exist_ok=True)
    js = ("window.MEDIA=" + json.dumps(mappa, separators=(",", ":")) + ";\n"
          "window.VIDEO=" + json.dumps(video, separators=(",", ":")) + ";")
    open(os.path.join(OUT, "media.js"), "w").write(js)
    json.dump(["media.js"] + sorted(video.values()),
              open(os.path.join(OUT, "files.json"), "w"))

    # la pagina pubblicata non porta doctype/html/head/body: li mette la piattaforma
    corpo = html
    corpo = corpo[corpo.index("<title>"):]
    corpo = corpo.replace("</head><body>", "\n", 1)
    corpo = corpo.replace("</body></html>", "", 1)
    corpo = corpo.replace('</div><script>', '</div>\n<script src="media.js"></script>\n<script>', 1)
    open(os.path.join(OUT, "index.html"), "w").write(corpo)

    print(f"anteprime: {len(mappa)}/{len(ids)} · webp {pesi/1e6:.1f} MB")
    print(f"video: {len(video)} inclusi, {fuori} fuori budget · {speso/1e6:.1f} MB")
    print(f"media.js {len(js)/1e6:.1f} MB · pagina {len(corpo)/1e6:.2f} MB · "
          f"{len(video)+1} file da pubblicare (elenco in build/files.json)")


if __name__ == "__main__":
    main()
