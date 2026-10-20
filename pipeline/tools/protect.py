#!/usr/bin/env python3
"""
protect.py — cifra la pagina Top Ads e lascia sul sito solo ads/index.html.

La password arriva da TAG_ADS_PASSWORD. I dati stanno gia' dentro l'HTML
(pipeline/templates/tag_ads.html + ads/data.json): si cifrano con AES-256-GCM
e una chiave ricavata con PBKDF2-HMAC-SHA256, 600.000 iterazioni. Il browser
decifra con WebCrypto. data.json e data.js vengono cancellati sempre.

Se la pagina gia' pubblicata si decifra nello stesso HTML, non si riscrive:
il sale e l'IV cambiano a ogni cifratura e rifarla farebbe un commit vuoto.

    python tools/protect.py ads
    python tools/protect.py --self-test

Dipende dal pacchetto "cryptography" (AES-GCM). PBKDF2 e' quello della stdlib.
"""
import base64
import hashlib
import json
import os
import re
import sys
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(ROOT, "templates", "tag_ads.html")
SITE = os.environ.get("RADAR_SITE") or os.path.join(ROOT, "site")
ITER = 600_000
MARKER = '<script src="data.js"></script>'
# Frasi del template: lo spazio non puo' capitare nel base64 del cifrato.
CHIARO = (
    "Tutte le ads lanciate da TAG",
    "act_1943610831146",
)

SHELL = """<!doctype html>
<html lang="it"><head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Top Ads TAG</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#15150f;--muted:#6b6b60;--line:#e3e3dc;--accent:#1a5f4a}
@media (prefers-color-scheme:dark){:root{--bg:#12120f;--card:#1b1b17;--ink:#f0efe8;--muted:#9a9a8d;--line:#2c2c25;--accent:#5fbfa0}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--ink);font:16px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif}
main{width:min(420px,calc(100% - 32px));background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px 24px}
h1{font-size:22px;margin:0 0 6px;letter-spacing:-.02em}
p{margin:0;color:var(--muted);font-size:14px}
form{margin-top:18px;display:flex;flex-direction:column;gap:10px}
label{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
input{font:inherit;font-size:16px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
button{font:inherit;font-weight:600;padding:10px 14px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
button:disabled{opacity:.6;cursor:progress}
.err{color:#b4471f;font-size:14px}
@media (prefers-color-scheme:dark){.err{color:#e88a5f}}
</style></head><body>
<main>
<h1>Top Ads TAG</h1>
<p>Pagina riservata. Inserisci la password per aprirla.</p>
<form id="f">
<label for="pw">Password</label>
<input id="pw" name="pw" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Entra</button>
<p class="err" id="err" hidden>Password non corretta.</p>
<p id="busy" hidden>Controllo la password…</p>
</form>
<noscript><p class="err">Per aprire la pagina serve JavaScript.</p></noscript>
</main>
<script type="application/json" id="tag-ads-cifrato">__PAYLOAD__</script>
<script>
const spec = JSON.parse(document.getElementById('tag-ads-cifrato').textContent);
const err = document.getElementById('err');
const busy = document.getElementById('busy');
const pw = document.getElementById('pw');
const btn = document.querySelector('button');
function b64(s){const bin = atob(s); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out;}
async function apri(password){
  if (!crypto.subtle) throw new Error('no-webcrypto');
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey(
    {name:'PBKDF2', salt:b64(spec.salt), iterations:spec.iter, hash:'SHA-256'},
    material, {name:'AES-GCM', length:256}, false, ['decrypt']);
  const plain = await crypto.subtle.decrypt({name:'AES-GCM', iv:b64(spec.iv)}, key, b64(spec.ct));
  const html = new TextDecoder().decode(plain);
  document.open();
  document.write(html);
  document.close();
}
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  err.hidden = true;
  busy.hidden = false;
  btn.disabled = true;
  try { await apri(pw.value); }
  catch (ex) {
    busy.hidden = true;
    btn.disabled = false;
    err.hidden = false;
    err.textContent = crypto.subtle ? 'Password non corretta.' : 'Questo browser non può aprire la pagina. Servono HTTPS e un browser aggiornato.';
    pw.focus();
    pw.select();
  }
});
</script>
</body></html>
"""


def chiave(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER, dklen=32)


def cifra(plain, password):
    salt = os.urandom(16)
    iv = os.urandom(12)
    ct = AESGCM(chiave(password, salt)).encrypt(iv, plain, None)
    return {
        "v": 1,
        "iter": ITER,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def decifra(spec, password):
    if spec.get("iter") != ITER:
        raise ValueError("iterazioni diverse")
    salt = base64.b64decode(spec["salt"])
    iv = base64.b64decode(spec["iv"])
    ct = base64.b64decode(spec["ct"])
    return AESGCM(chiave(password, salt)).decrypt(iv, ct, None)


def js_json(data):
    """JSON che si puo' incollare in uno <script> senza chiuderlo."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def componi(data):
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    if MARKER not in template:
        sys.exit("il template non include data.js")
    payload = "<script>window.TAG_DATA=" + js_json(data) + ";</script>"
    return template.replace(MARKER, payload, 1)


def estrai(html):
    match = re.search(
        r'<script type="application/json" id="tag-ads-cifrato">(.*?)</script>',
        html, re.S)
    if not match:
        return None
    return json.loads(match.group(1))


def shell_di(spec):
    payload = json.dumps(spec, separators=(",", ":"))
    if "<" in payload:
        sys.exit("payload inatteso")
    page = SHELL.replace("__PAYLOAD__", payload, 1)
    if "__PAYLOAD__" in page:
        sys.exit("segnaposto non sostituito")
    for frase in CHIARO:
        if frase in page:
            sys.exit("rifiutato: la pagina cifrata contiene testo in chiaro")
    return page


def gia_uguale(index_path, plain, password):
    if not os.path.isfile(index_path):
        return False
    try:
        with open(index_path, encoding="utf-8") as f:
            spec = estrai(f.read())
        if not spec:
            return False
        return decifra(spec, password) == plain.encode("utf-8")
    except (InvalidTag, ValueError, KeyError, json.JSONDecodeError, TypeError):
        return False


def scrivi(index_path, plain, password):
    page = shell_di(cifra(plain.encode("utf-8"), password))
    directory = os.path.dirname(index_path)
    fd, tmp = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(page)
        os.replace(tmp, index_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return page


def pulisci(directory):
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        if name in ("data.json", "data.js") or (name.startswith(".index.") and name.endswith(".tmp")):
            os.remove(os.path.join(directory, name))


def proteggi(name, password):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        sys.exit("nome cartella non valido")
    if not password:
        sys.exit("manca TAG_ADS_PASSWORD")
    out_dir = os.path.join(SITE, name)
    os.makedirs(out_dir, exist_ok=True)
    data_path = os.path.join(out_dir, "data.json")
    index_path = os.path.join(out_dir, "index.html")
    try:
        try:
            with open(data_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            sys.exit(f"manca {data_path}: lancia prima tools/tag_ads.py")
        plain = componi(data)
        if gia_uguale(index_path, plain, password):
            print(f"{name}/index.html gia' allineata, non la riscrivo")
        else:
            scrivi(index_path, plain, password)
            print(f"cifrata {index_path} ({os.path.getsize(index_path)} byte)")
    finally:
        pulisci(out_dir)


def self_test():
    """Cifra una pagina finta e controlla che il chiaro non resti sul disco."""
    import shutil
    password = "self-test-password"
    secret = "SPESA-SEGRETA-NON-PUBBLICARE"
    data = {
        "generated": "2026-10-20T09:00",
        "until": "2026-10-19",
        "windows": {
            "30": {"since": "2026-09-20", "campaigns": 1, "ads": [{
                "id": "1", "ad": secret, "camp": "c", "adset": "s",
                "course": "Non-corso", "cf": "", "fmt": "Static",
                "spend": 10, "impr": 100, "clicks": 5, "link": 4, "lead": 1,
            }]},
            "90": {"since": "2026-07-21", "campaigns": 0, "ads": []},
            "180": {"since": "2026-04-21", "campaigns": 0, "ads": []},
        },
        "creatives": {"1": {"th": "https://example.invalid/a.png", "pv": "https://example.invalid/p"}},
    }
    tmp = tempfile.mkdtemp(prefix="tag-ads-")
    old_site = os.environ.get("RADAR_SITE")
    os.environ["RADAR_SITE"] = tmp
    global SITE
    SITE = tmp
    try:
        os.makedirs(os.path.join(tmp, "ads"))
        with open(os.path.join(tmp, "ads", "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        with open(os.path.join(tmp, "ads", "data.js"), "w", encoding="utf-8") as f:
            f.write("window.TAG_DATA={};")
        proteggi("ads", password)
        index = os.path.join(tmp, "ads", "index.html")
        page = open(index, encoding="utf-8").read()
        if secret in page or "data.json" in os.listdir(os.path.join(tmp, "ads")):
            sys.exit("self-test: il chiaro e' rimasto")
        if os.path.exists(os.path.join(tmp, "ads", "data.js")):
            sys.exit("self-test: data.js rimasto")
        spec = estrai(page)
        plain = decifra(spec, password).decode("utf-8")
        if secret not in plain:
            sys.exit("self-test: il testo non torna decifrato")
        try:
            decifra(spec, "sbagliata")
            sys.exit("self-test: una password sbagliata ha decifrato")
        except InvalidTag:
            pass
        prima = open(index, "rb").read()
        with open(os.path.join(tmp, "ads", "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        proteggi("ads", password)
        if open(index, "rb").read() != prima:
            sys.exit("self-test: riscritta una pagina identica")
        print("self-test ok")
    finally:
        if old_site is None:
            os.environ.pop("RADAR_SITE", None)
        else:
            os.environ["RADAR_SITE"] = old_site
        SITE = os.environ.get("RADAR_SITE") or os.path.join(ROOT, "site")
        shutil.rmtree(tmp)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        self_test()
        return
    if len(sys.argv) != 2:
        sys.exit("uso: python tools/protect.py ads")
    proteggi(sys.argv[1], os.environ.get("TAG_ADS_PASSWORD", ""))


if __name__ == "__main__":
    main()
