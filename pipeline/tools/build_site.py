#!/usr/bin/env python3
"""
build_site.py — impacchetta la dashboard come sito statico, da mettere su un dominio.

Differenza dal pacchetto per l'anteprima condivisa: qui i media sono file veri, non
data URI dentro un JS. Il browser li mette in cache, la pagina si apre subito, e non
c'e' il tetto dei 64 MB: entrano tutti i video, a qualita' piu' alta.

    ~/tools/Scrapling/.venv/bin/python tools/build_site.py
    -> site/  (index.html · media/ · v/)
"""
import io, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
# In cloud il sito va scritto direttamente nella copia del repo pubblicato, non in una
# sottocartella: RADAR_SITE dice dove.
SITE = os.environ.get("RADAR_SITE") or os.path.join(ROOT, "site")

LATO, QUALITA = 420, 72                       # le card mostrano ~282px, il doppio su retina
VCRF, VLARGO, VFPS, VAUDIO = "32", "480", "25", "64k"

# Pages regge circa 1 GB per repo, e un repo che cresce ogni settimana ci arriva. Il
# tetto decide in che ordine spendere lo spazio invece di riempirlo a caso: prima un
# video per concetto, poi i doppioni finche' ce n'e'.
BUDGET_VIDEO = int(os.environ.get("RADAR_BUDGET_VIDEO") or 420_000_000)


def webp(sorgente, destinazione):
    from PIL import Image
    im = Image.open(sorgente).convert("RGB")
    w, h = im.size
    if max(w, h) > LATO:
        f = LATO / max(w, h)
        im = im.resize((round(w * f), round(h * f)), Image.LANCZOS)
    im.save(destinazione, "WEBP", quality=QUALITA, method=6)
    return os.path.getsize(destinazione)


def video(sorgente, destinazione):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", sorgente,
         "-vf", f"scale='min({VLARGO},iw)':-2:flags=lanczos",
         "-c:v", "libx264", "-crf", VCRF, "-preset", "veryfast", "-r", VFPS,
         "-c:a", "aac", "-b:a", VAUDIO, "-ac", "1",
         "-movflags", "+faststart", destinazione], check=True)
    return os.path.getsize(destinazione)


def per_importanza(ads):
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
    m = re.search(r"const D = (\{.*?\});\n", html, re.S)
    dati = json.loads(m.group(1))

    # incrementale: i media gia' convertiti non si rifanno, cosi' una correzione alla
    # pagina non costa dieci minuti di ricodifica
    os.makedirs(os.path.join(SITE, "media"), exist_ok=True)
    os.makedirs(os.path.join(SITE, "v"), exist_ok=True)

    pesoi = pesov = 0
    nimg = nvid = 0
    for a in dati["ads"]:
        nuovi = []
        for rel in (a.get("thumbs") or []):
            fp = os.path.join(DOCS, rel)
            if not os.path.exists(fp):
                continue
            out_rel = rel.rsplit(".", 1)[0] + ".webp"
            out_fp = os.path.join(SITE, out_rel)
            if os.path.exists(out_fp) and os.path.getsize(out_fp) > 500:
                pesoi += os.path.getsize(out_fp)
            else:
                try:
                    pesoi += webp(fp, out_fp)
                except Exception:
                    continue
            nuovi.append(out_rel)
            nimg += 1
        if nuovi:
            a["thumbs"], a["thumb"] = nuovi, nuovi[0]
        else:
            a.pop("thumbs", None), a.pop("thumb", None)

        vl = None  # i video si fanno dopo, in ordine di importanza e dentro il budget
        if vl and os.path.exists(os.path.join(DOCS, vl)):
            out_rel = f"v/{a['ad_archive_id']}.mp4"
            out_fp = os.path.join(SITE, out_rel)
            if os.path.exists(out_fp) and os.path.getsize(out_fp) > 20000:
                pesov += os.path.getsize(out_fp)
                a["video_local"] = out_rel
                nvid += 1
            else:
                try:
                    pesov += video(os.path.join(DOCS, vl), out_fp)
                    a["video_local"] = out_rel
                    nvid += 1
                except subprocess.CalledProcessError:
                    a.pop("video_local", None)

    speso, fuori = 0, 0
    for a in per_importanza([x for x in dati["ads"] if x.get("video_local")]):
        sorgente = os.path.join(DOCS, a["video_local"])
        out_rel = f"v/{a['ad_archive_id']}.mp4"
        out_fp = os.path.join(SITE, out_rel)
        if os.path.exists(out_fp) and os.path.getsize(out_fp) > 20000:
            n = os.path.getsize(out_fp)
        elif os.path.exists(sorgente):
            try:
                n = video(sorgente, out_fp)
            except subprocess.CalledProcessError:
                a.pop("video_local", None)
                continue
        else:
            a.pop("video_local", None)
            continue
        if speso + n > BUDGET_VIDEO:
            os.remove(out_fp)
            a.pop("video_local", None)
            fuori += 1
            continue
        speso += n
        pesov += n
        nvid += 1
        a["video_local"] = out_rel

    # chi non e' piu' nel catalogo (competitor spento, inserzione sparita) non deve
    # restare a pesare nel repo: il pacchetto e' incrementale, non accumulativo
    servono = {r for a in dati["ads"] for r in (a.get("thumbs") or [])}
    servono |= {a["video_local"] for a in dati["ads"] if a.get("video_local")}
    orfani = 0
    for cartella in ("media", "v"):
        d = os.path.join(SITE, cartella)
        for nome in os.listdir(d) if os.path.isdir(d) else []:
            rel = f"{cartella}/{nome}"
            if rel not in servono:
                os.remove(os.path.join(SITE, rel))
                orfani += 1
    if orfani:
        print(f"orfani rimossi: {orfani}")

    nuovo = html[:m.start(1)] + json.dumps(dati, ensure_ascii=False) + html[m.end(1):]
    # il sito e' raggiungibile da chiunque abbia il link, ma non deve finire nei motori
    nuovo = nuovo.replace('<meta charset="utf-8">\n',
                          '<meta charset="utf-8">\n'
                          '<meta name="robots" content="noindex, nofollow, noarchive">\n', 1)
    open(os.path.join(SITE, "index.html"), "w").write(nuovo)
    # niente Jekyll: le cartelle e i file passano cosi' come sono
    open(os.path.join(SITE, ".nojekyll"), "w").write("")

    print(f"immagini: {nimg} · {pesoi/1e6:.1f} MB")
    print(f"video:    {nvid} inclusi, {fuori} fuori budget · {pesov/1e6:.1f} MB")
    print(f"pagina:   {len(nuovo)/1e6:.2f} MB")
    print(f"sito:     {(pesoi+pesov+len(nuovo))/1e6:.0f} MB in site/")


if __name__ == "__main__":
    main()
