"""
EdNova platform reconnaissance — run ONLY inside your active slot.
Usage:
  pip install httpx python-dotenv
  .env:  LLM_BASE_URL=https://llm.felcloud.io/v1
         LLM_API_KEY=...            (never commit; .env is in .gitignore)
  python recon.py all            # full run (~60-80 requests, ~5 min)
  python recon.py models|latency|headers|ceilings|lang|json|embed

Design rules (the organizers log everything per key):
  * Paced at 1 request / 3.5 s, concurrency 1  -> never trips 20 rpm / 2 concurrent.
  * Every prompt carries a nonce -> recon does NOT inflate the "repeated identical
    requests" (cache) indicator.
  * 429/503 are logged and honored (Retry-After), never hammered.
  * The API key is never written to the log.
Output: recon_log.jsonl (one line per call) + recon_summary.json
"""
import json, os, sys, time, uuid, statistics
import httpx
from dotenv import load_dotenv

load_dotenv()
BASE = os.environ["LLM_BASE_URL"].rstrip("/")
KEY = os.environ["LLM_API_KEY"]
HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
PACE_S = 3.5
LOG = open("recon_log.jsonl", "a", encoding="utf-8")
SUMMARY = {}
_last = 0.0
client = httpx.Client(timeout=180)


def nonce():
    return uuid.uuid4().hex[:8]


def call(method, path, body=None, tag=""):
    """Paced single call; logs status, latency, ALL headers, usage, error body."""
    global _last
    wait = PACE_S - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        t0 = time.time()
        try:
            r = client.request(method, BASE + path, headers=HDR, json=body)
        except httpx.HTTPError as e:
            rec = {"tag": tag, "path": path, "error": repr(e), "lat": time.time() - t0}
            LOG.write(json.dumps(rec) + "\n"); LOG.flush()
            _last = time.time()
            return None, rec
        _last = time.time()
        lat = round(_last - t0, 2)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:500]}
        rec = {
            "ts": time.strftime("%H:%M:%S"), "tag": tag, "path": path,
            "model": (body or {}).get("model"), "status": r.status_code, "lat": lat,
            "headers": {k: v for k, v in r.headers.items() if k.lower() != "set-cookie"},
            "usage": data.get("usage") if isinstance(data, dict) else None,
            "err": data if r.status_code >= 400 else None,
        }
        LOG.write(json.dumps(rec, ensure_ascii=False) + "\n"); LOG.flush()
        if r.status_code in (429, 503):
            ra = r.headers.get("retry-after")
            delay = float(ra) if ra and ra.replace(".", "").isdigit() else 20
            print(f"  [{r.status_code}] {tag}: waiting {delay}s (Retry-After={ra})")
            time.sleep(min(delay, 120))
            continue
        return data, rec
    return None, rec


def chat(model, prompt, max_tokens=64, tag="", **extra):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0, **extra}
    return call("POST", "/chat/completions", body, tag)


def text_of(data):
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


# ---------------------------------------------------------------- probes
def p_models():
    data, rec = call("GET", "/models", tag="models")
    ids = [m.get("id") for m in (data or {}).get("data", [])]
    SUMMARY["models"] = ids
    SUMMARY["models_raw"] = data
    print("models:", ids)
    return ids


def chat_models(ids):
    return [m for m in ids if "embed" not in m.lower()] or ids


def p_latency(ids):
    """3 short + 1 long-output call per chat model. Gives ms/output-token."""
    res = {}
    for m in chat_models(ids):
        short = []
        for _ in range(3):
            d, r = chat(m, f"[{nonce()}] Reply with the single word: ok", 8, tag="lat_short")
            if r.get("status") == 200:
                short.append(r["lat"])
        d, r = chat(m, f"[{nonce()}] Explain what a derivative is to a 15-year-old in about 150 words.",
                    300, tag="lat_long")
        out_tok = ((r or {}).get("usage") or {}).get("completion_tokens")
        res[m] = {
            "short_median_s": statistics.median(short) if short else None,
            "long_s": r.get("lat"), "long_out_tokens": out_tok,
            "s_per_out_token": round(r["lat"] / out_tok, 3) if out_tok else None,
        }
        print(m, res[m])
    SUMMARY["latency"] = res


def p_headers():
    """Collect header names from logged 200s -> discover quota header names."""
    names = set()
    with open("recon_log.jsonl", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == 200:
                names.update(rec.get("headers", {}).keys())
    quota_like = sorted(n for n in names if any(k in n.lower() for k in
                        ("rate", "limit", "quota", "remaining", "reset", "retry", "bucket", "token")))
    SUMMARY["quota_headers"] = quota_like
    SUMMARY["all_headers"] = sorted(names)
    print("quota-like headers:", quota_like)


def p_ceilings(ids):
    """Find the max_tokens ceiling per model (expect a 400 naming the ceiling)."""
    res = {}
    for m in chat_models(ids):
        ok, bad, msg = None, None, None
        for mt in (512, 1024, 2048, 4096, 8192):
            d, r = chat(m, f"[{nonce()}] Say hi.", mt, tag=f"ceil_{mt}")
            if r.get("status") == 200:
                ok = mt
            elif r.get("status") == 400:
                bad, msg = mt, r.get("err")
                break
        res[m] = {"max_tokens_ok": ok, "first_400_at": bad, "error": msg}
        print(m, res[m])
    # input-length ceiling, compact-ish model only (cheap): ~6k tokens of filler
    m = chat_models(ids)[0]
    filler = ("The learner studies functions. " * 1000)
    d, r = chat(m, f"[{nonce()}] {filler}\nSay ok.", 8, tag="ceil_input")
    res["input_6k_tokens"] = {"model": m, "status": r.get("status"), "err": r.get("err"),
                              "prompt_tokens": ((r or {}).get("usage") or {}).get("prompt_tokens")}
    SUMMARY["ceilings"] = res


LANG_CASES = [
    ("darija_arabizi", "chnowa l fer9 bin derivee w primitive? ma fhemtch"),
    ("darija_arabic", "شنوة الفرق بين الدالة المشتقة والدالة الأصلية؟ ما فهمتش"),
    ("msa", "ما الفرق بين المشتقة والدالة الأصلية؟"),
    ("french", "Je ne comprends pas la différence entre dérivée et primitive."),
    ("english", "I don't get the difference between a derivative and an antiderivative."),
    ("mixed", "svp chnowa ya3ni limite quand x tend vers l'infini?"),
]
CLASSIFY = ('Return ONLY JSON: {"language": "darija|msa|french|english|mixed", '
            '"intent": "question|answer|confused|chitchat", "concept": "<short>", '
            '"needs_reasoning": true|false}. Message: ')


def p_lang(ids):
    """Can each model classify Darija/Arabizi? Can it REPLY in Darija?"""
    res = {}
    for m in chat_models(ids):
        res[m] = {}
        for name, msg in LANG_CASES:
            d, r = chat(m, f"[{nonce()}] {CLASSIFY}{msg}", 120, tag=f"lang_{name}")
            res[m][name] = text_of(d)
        d, r = chat(m, f"[{nonce()}] Explique en darija tunisienne (lettres latines), en 3 phrases, "
                       "c'est quoi une dérivée.", 200, tag="darija_gen")
        res[m]["darija_generation"] = text_of(d)
    SUMMARY["lang"] = res
    print("lang results saved — judge them by eye (a Tunisian reader must grade these).")


def p_json(ids):
    """Is response_format json_object supported? Does output parse?"""
    res = {}
    for m in chat_models(ids):
        d, r = chat(m, f"[{nonce()}] {CLASSIFY}{LANG_CASES[3][1]}", 120, tag="json_mode",
                    response_format={"type": "json_object"})
        t = text_of(d)
        try:
            json.loads(t); parsed = True
        except Exception:
            parsed = False
        res[m] = {"status": r.get("status"), "parsed": parsed, "err": r.get("err"), "text": t}
    SUMMARY["json_mode"] = res


def p_embed(ids):
    em = [m for m in ids if "embed" in m.lower()]
    model = em[0] if em else (os.environ.get("EMBED_MODEL") or ids[0])
    d, r = call("POST", "/embeddings", {"model": model, "input": [f"{nonce()} derivative", "dérivée"]},
                tag="embed")
    vecs = (d or {}).get("data", [])
    # batch-size probe: 32 inputs in one request (counts as ONE request on the bucket)
    d2, r2 = call("POST", "/embeddings",
                  {"model": model, "input": [f"{nonce()} chunk {i}" for i in range(32)]}, tag="embed_batch32")
    SUMMARY["embeddings"] = {"model": model, "status": r.get("status"),
                             "dim": len(vecs[0]["embedding"]) if vecs else None,
                             "batch32_status": r2.get("status"), "batch32_err": r2.get("err"),
                             "headers": r.get("headers")}
    print(SUMMARY["embeddings"]["dim"], "dims; batch32:", r2.get("status"))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    ids = p_models()
    steps = {"latency": p_latency, "ceilings": p_ceilings, "lang": p_lang,
             "json": p_json, "embed": p_embed}
    order = list(steps) if what == "all" else [what] if what in steps else []
    for s in order:
        print(f"== {s}")
        steps[s](ids)
    p_headers()
    json.dump(SUMMARY, open("recon_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("done -> recon_summary.json")
