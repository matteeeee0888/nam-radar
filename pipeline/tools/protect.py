#!/usr/bin/env python3
"""
protect.py — cifra la pagina Top Ads e cancella i dati in chiaro.

    TAG_ADS_PASSWORD=... RADAR_SITE=... python tools/protect.py ads

Legge $RADAR_SITE/ads/data.json e pipeline/templates/tag_ads.html, mette i
dati dentro la pagina e scrive $RADAR_SITE/ads/index.html. La pagina pubblica
chiede la password e decifra nel browser (PBKDF2-SHA256, 600k iterazioni,
AES-256-GCM). Il json non resta sul disco.
"""
import base64
import json
import os
import sys
import unicodedata

ITERAZIONI = 600_000
SALT_LEN = 16
IV_LEN = 12

SHELL = r"""<!doctype html>
<html lang="it"><head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache">
<title>Top Ads TAG</title>
<script>try{if(sessionStorage.getItem("tag-ads-pw"))document.documentElement.classList.add("riapre");}catch(e){}</script>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#15150f;--muted:#6b6b60;--line:#e3e3dc;--accent:#1a5f4a;--err:#b4471f}
@media (prefers-color-scheme:dark){:root{--bg:#12120f;--card:#1b1b17;--ink:#f0efe8;--muted:#9a9a8d;--line:#2c2c25;--accent:#5fbfa0;--err:#e88a5f}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif}
main{width:min(400px,calc(100% - 32px));background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px 24px}
h1{font-size:22px;margin:0 0 8px;letter-spacing:-.02em}
p{color:var(--muted);margin:0 0 16px;font-size:14px}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}
input{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
input:focus{outline:2px solid var(--accent);outline-offset:1px}
button{margin-top:14px;width:100%;font:inherit;font-weight:600;padding:10px 12px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
button:disabled{opacity:.65;cursor:progress}
#err{color:var(--err);min-height:1.2em;margin:10px 0 0;font-size:13px}
#wait{display:none}
.riapre #gate{display:none}
.riapre #wait{display:block}
</style></head><body>
<main>
<h1>Top Ads TAG</h1>
<p id="wait">Apro la pagina…</p>
<form id="gate" method="post" action="#sblocca" autocomplete="on">
<p>Pagina riservata. La password resta in questo browser e non viene inviata a un server.</p>
<label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autocapitalize="off" spellcheck="false" required autofocus>
<button type="submit" id="go">Entra</button>
<p id="err" hidden></p>
</form>
</main>
<script type="application/json" id="payload">__PAYLOAD_JSON__</script>
<script>
const P = JSON.parse(document.getElementById("payload").textContent);
const KEY = "tag-ads-pw";
const err = document.getElementById("err");
const go = document.getElementById("go");
function b64(s){const b=atob(s);const u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u}
function messaggio(t){err.hidden=!t;err.textContent=t||""}
async function decifra(pw){
  if(!crypto.subtle) throw new Error("browser");
  const mat = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    {name:"PBKDF2", salt:b64(P.salt), iterations:P.iter, hash:"SHA-256"},
    mat, {name:"AES-GCM", length:256}, false, ["decrypt"]);
  const buf = await crypto.subtle.decrypt({name:"AES-GCM", iv:b64(P.iv)}, key, b64(P.ct));
  return new TextDecoder().decode(buf);
}
function mostra(html){
  const doc = new DOMParser().parseFromString(html, "text/html");
  document.replaceChild(document.importNode(doc.documentElement, true), document.documentElement);
  for (const old of document.querySelectorAll("script")) {
    const s = document.createElement("script");
    for (const attr of old.attributes) s.setAttribute(attr.name, attr.value);
    s.textContent = old.textContent;
    old.replaceWith(s);
  }
}
async function apri(pw){
  messaggio("");
  go.disabled = true;
  try {
    const html = await decifra(pw);
    sessionStorage.setItem(KEY, pw);
    mostra(html);
  } catch (e) {
    sessionStorage.removeItem(KEY);
    document.documentElement.classList.remove("riapre");
    document.getElementById("gate").hidden = false;
    go.disabled = false;
    messaggio(e && e.message === "browser"
      ? "Questo browser non può aprire la pagina. Usa Chrome, Firefox o Safari aggiornati."
      : "Password errata.");
    document.getElementById("pw").focus();
  }
}
document.getElementById("gate").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const pw = document.getElementById("pw").value.trim().normalize("NFC");
  if (!pw) { messaggio("Scrivi la password."); return; }
  apri(pw);
});
const salvata = sessionStorage.getItem(KEY);
if (salvata) apri(salvata);
</script>
</body></html>
"""


def password():
    pw = unicodedata.normalize("NFC", os.environ.get("TAG_ADS_PASSWORD", "").strip())
    if not pw:
        sys.exit("manca il secret TAG_ADS_PASSWORD")
    return pw


def percorsi():
    qui = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.dirname(qui)
    repo = os.path.dirname(pipeline)
    site = os.environ.get("RADAR_SITE") or repo
    return os.path.join(pipeline, "templates", "tag_ads.html"), os.path.join(site, "ads")


def js_json(obj):
    testo = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return (testo.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def cifra(html, pw):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = os.urandom(SALT_LEN)
    iv = os.urandom(IV_LEN)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERAZIONI)
    key = kdf.derive(pw.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)
    verifica = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERAZIONI)
    chiaro = AESGCM(verifica.derive(pw.encode("utf-8"))).decrypt(iv, ct, None)
    if chiaro != html.encode("utf-8"):
        sys.exit("verifica della cifratura fallita")
    return salt, iv, ct


def cancella_chiaro(cartella):
    for nome in ("data.json", "data.js", ".data.json.partial", ".index.html.tmp"):
        path = os.path.join(cartella, nome)
        if os.path.isfile(path):
            os.remove(path)


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "ads":
        sys.exit("uso: python tools/protect.py ads")
    template_path, cartella = percorsi()
    dati_path = os.path.join(cartella, "data.json")
    try:
        pw = password()
        if not os.path.isfile(dati_path):
            sys.exit("manca ads/data.json: lancia prima tools/tag_ads.py")
        if not os.path.isfile(template_path):
            sys.exit("manca il template tag_ads.html")
        with open(dati_path, encoding="utf-8") as f:
            raw = f.read()
        if len(raw.encode("utf-8")) > 20_000_000:
            sys.exit("data.json è troppo grande")
        dati = json.loads(raw)
        finestre = dati.get("windows") or {}
        if any(k not in finestre for k in ("30", "90", "180")):
            sys.exit("data.json senza le finestre 30, 90 e 180")
        with open(template_path, encoding="utf-8") as f:
            template = f.read()
        pezzi = template.split("__TAG_DATA_JSON__")
        if len(pezzi) != 2:
            sys.exit("il template non ha il segnaposto dei dati")
        html = pezzi[0] + js_json(dati) + pezzi[1]
        if "__TAG_DATA_JSON__" in pezzi[0] or "__TAG_DATA_JSON__" in pezzi[1]:
            sys.exit("segnaposto dati ripetuto")
        salt, iv, ct = cifra(html, pw)
        blob = json.dumps({
            "v": 1,
            "iter": ITERAZIONI,
            "salt": base64.b64encode(salt).decode("ascii"),
            "iv": base64.b64encode(iv).decode("ascii"),
            "ct": base64.b64encode(ct).decode("ascii"),
        }, separators=(",", ":"))
        if "<" in blob or SHELL.count("__PAYLOAD_JSON__") != 1:
            sys.exit("payload non pubblicabile")
        pagina = SHELL.replace("__PAYLOAD_JSON__", blob)
        if "window.TAG_DATA" in pagina or "__TAG_DATA_JSON__" in pagina:
            sys.exit("la pagina cifrata contiene dati in chiaro")
        os.makedirs(cartella, exist_ok=True)
        tmp = os.path.join(cartella, ".index.html.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(pagina)
        os.replace(tmp, os.path.join(cartella, "index.html"))
    finally:
        cancella_chiaro(cartella)
    sommario = os.environ.get("GITHUB_STEP_SUMMARY")
    if sommario:
        with open(sommario, "a", encoding="utf-8") as f:
            f.write("\n- Pagina Top ads cifrata in /ads/\n")
    print("ads/index.html cifrato")


if __name__ == "__main__":
    main()
