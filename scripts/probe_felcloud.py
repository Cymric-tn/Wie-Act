"""Raw httpx probe of the real FelCloud API (bypasses the engine). Synthetic prompts only.

    ENV_FILE=<felcloud profile> python -m scripts.probe_felcloud models|headers|maxtokens|truncation <chars/token> [end]|refusals
        |window|outlen|ceiling <chars/token>|chars|ocr <outdir>|compare <compact model>

Every exchange (status, response headers, body) is appended to reports/felcloud-raw.jsonl with the key
redacted; request headers are never stored. `refusals` deliberately triggers 429s.
Each refusal is also run through gatekeeper.errors.classify_response to show which typed error it becomes.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time

import httpx

from config import ModelClass, load_settings
from gatekeeper.errors import classify_response

RAW = "reports/felcloud-raw.jsonl"
KNOWN = {"llama3.2-3b", "qwen2.5-3b", "llama3.1-8b", "mistral-7b", "qwen2.5-coder-7b", "deepseek-ocr"}
SETTINGS = load_settings()
SENT = 0


def redact(s: str) -> str:
    s = s.replace(SETTINGS.api_key, "***") if SETTINGS.api_key else s
    s = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", s)
    return re.sub(r"(api_key: )[0-9a-f]{64}", r"\1<key-hash>", s)   # 429 bodies echo a hash of the key


def log(step: str, label: str, req: dict | None, resp: httpx.Response | None, ms: float, err: str = "") -> dict:
    rec = {"ts": round(time.time(), 3), "step": step, "label": label, "request": req,
           "status": resp.status_code if resp is not None else None,
           "headers": {k: redact(v) for k, v in resp.headers.items()} if resp is not None else None,
           "body": redact(resp.text[:4000]) if resp is not None else None, "elapsed_ms": round(ms, 1)}
    if err:
        rec["error"] = err
    if resp is not None and resp.status_code >= 400:
        t = classify_response(resp)
        rec["typed_error"] = type(t).__name__ if t else None
        rec["retry_after_s"] = getattr(t, "retry_after_s", None)
    with open(RAW, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


async def call(c: httpx.AsyncClient, step: str, label: str, method: str, path: str, body: dict | None = None,
               store_body: bool = True) -> dict:
    global SENT
    SENT += 1
    t0 = time.perf_counter()
    req = {"method": method, "path": path,
           "body": (body if store_body else {k: v for k, v in (body or {}).items() if k != "messages"})}
    if not store_body and body:
        req["messages_chars"] = [len(m["content"]) if isinstance(m["content"], str) else len(json.dumps(m["content"])) for m in body["messages"]]
    try:
        r = await c.request(method, path, json=body)
        rec = log(step, label, req, r, (time.perf_counter() - t0) * 1000)
    except httpx.HTTPError as e:
        rec = log(step, label, req, None, (time.perf_counter() - t0) * 1000, err=type(e).__name__)
    return rec


def chat_body(model: str, user: str, system: str = "", **kw) -> dict:
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    return {"model": model, "messages": msgs, "temperature": 0, **kw}


def summarize(rec: dict) -> str:
    h = rec.get("headers") or {}
    rl = {k.replace("x-ratelimit-api_key-", ""): v for k, v in h.items() if k.startswith("x-ratelimit")}
    out = f"status={rec['status']} {rec['elapsed_ms']:.0f}ms"
    try:
        d = json.loads(rec["body"])
        ch = d["choices"][0]
        out += (f" finish={ch.get('finish_reason')} usage={d.get('usage', {}).get('prompt_tokens')}/"
                f"{d.get('usage', {}).get('completion_tokens')}")
    except (ValueError, KeyError, TypeError, IndexError):
        if rec.get("body"):
            out += f" body={rec['body'][:160]!r}"
    if rl:
        out += f" ratelimit={rl}"
    if "typed_error" in rec:
        out += f" -> {rec['typed_error']}(retry_after={rec.get('retry_after_s')})"
    if rec.get("error"):
        out += f" error={rec['error']}"
    return out


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=SETTINGS.base_url, timeout=90.0,
                             headers={"Authorization": f"Bearer {SETTINGS.api_key}"})


def model(cls: ModelClass) -> str:
    return SETTINGS.models[cls][0]


async def models() -> None:
    async with client() as c:
        rec = await call(c, "models", "GET /models", "GET", "/models")
    print(summarize(rec))
    if rec["status"] in (401, 403):
        print("STOP: key or slot rejected"); sys.exit(2)
    ids = [m["id"] for m in json.loads(rec["body"]).get("data", [])]
    print("models:", ids)
    for cls in ModelClass:
        for m in SETTINGS.models.get(cls, ()):
            print(f"  configured {cls.value}: {m} -> {'present' if m in ids else 'MISSING'}")
    for m in ids:
        size = re.search(r"(\d+(?:\.\d+)?)b\b", m)
        if m not in KNOWN or re.search(r"qwen3|gemma", m) or (size and float(size.group(1)) > 8):
            print(f"  FLAG new/large model: {m}")


SYS = "Classify the learner message. Reply with one JSON object: intent, language, confusion (LOW|MEDIUM|HIGH)."
MSGS = ["Je n'ai pas compris la dérivée d'un produit, peux-tu m'expliquer ?",
        "How do I find the tangent line of f(x) = x^2 at x = 3?",
        "chnowa ya3ni limite? ma fhemt chay"]
FR = ("La dérivée d'une fonction mesure la variation instantanée de cette fonction. Pour un polynôme, "
      "on dérive terme par terme en appliquant la règle des puissances. ")


async def headers() -> None:
    comp, mid = model(ModelClass.COMPACT), model(ModelClass.MIDSIZE)
    async with client() as c:
        for i, m in enumerate(MSGS):
            rec = await call(c, "headers", f"classify-style {i}", "POST", "/chat/completions",
                             chat_body(comp, m, SYS, max_tokens=120))
            print(f"compact classify-style {i}: {summarize(rec)}")
        for name, mdl in (("compact", comp), ("midsize", mid)):
            rec = await call(c, "headers", f"json_object {name}", "POST", "/chat/completions",
                             chat_body(mdl, 'Reply with {"ok": true} as JSON.', max_tokens=30,
                                       response_format={"type": "json_object"}))
            print(f"json_object {name}: {summarize(rec)}")
        for name, mdl in (("compact", comp), ("midsize", mid)):
            rec = await call(c, "headers", f"frequency_penalty {name}", "POST", "/chat/completions",
                             chat_body(mdl, "Say hello in French.", max_tokens=20, frequency_penalty=0.3))
            print(f"frequency_penalty=0.3 {name}: {summarize(rec)}")
        text = FR * 20
        rec = await call(c, "headers", "calibration chars->tokens", "POST", "/chat/completions",
                         chat_body(comp, text, max_tokens=1), store_body=False)
        print(f"calibration ({len(text)} chars): {summarize(rec)}")


def build(chars_per_token: float, target_tokens: int, at_end: bool = False) -> str:
    n = int(target_tokens * chars_per_token) - 200
    mid = (FR * (n // len(FR) + 1))[:n]
    if at_end:   # catches truncation of the END, which the start-anchored variant cannot see
        return "Lis ce texte, puis obéis à la dernière consigne.\n\n" + mid + "\n\nDernière consigne : réponds uniquement par le mot MANGUE."
    return ("Answer only with the word PAPAYA.\n\n" + mid + "\n\nWhat is the word?")


async def truncation(cpt: float, at_end: bool = False, targets=(2600, 1700), step: str = "truncation") -> None:
    comp = model(ModelClass.COMPACT)
    async with client() as c:
        for target in targets:
            rec = await call(c, step, f"{'END-MANGUE' if at_end else 'PAPAYA'} ~{target} real tokens", "POST", "/chat/completions",
                             chat_body(comp, build(cpt, target, at_end), max_tokens=20), store_body=False)
            ans = ""
            try:
                ans = json.loads(rec["body"])["choices"][0]["message"]["content"]
            except (ValueError, KeyError, TypeError, IndexError):
                pass
            print(f"target {target}: {summarize(rec)} answer={ans[:80]!r} found={('MANGUE' if at_end else 'PAPAYA') in ans.upper()}")


async def maxtokens() -> None:
    """Is max_tokens enforced? Long-answer prompt with small and engine-sized limits, both classes."""
    ask = "Écris un long texte détaillé (au moins 300 mots) sur la dérivée en mathématiques."
    async with client() as c:
        for cls in (ModelClass.COMPACT, ModelClass.MIDSIZE):
            for mt in (10, 60):
                rec = await call(c, "maxtokens", f"{cls.value} max_tokens={mt}", "POST", "/chat/completions",
                                 chat_body(model(cls), ask, max_tokens=mt))
                print(f"{cls.value} max_tokens={mt}: {summarize(rec)}")


async def refusals() -> None:
    comp = model(ModelClass.COMPACT)
    tiny = lambda: chat_body(comp, "Say ok.", max_tokens=5)  # noqa: E731
    async with client() as c:
        print("-- concurrency: 4 simultaneous")
        res = await asyncio.gather(*[call(c, "refusals", f"concurrent {i}", "POST", "/chat/completions", tiny())
                                     for i in range(4)])
        for i, r in enumerate(res):
            print(f"  #{i}: {summarize(r)}")
        print("-- waiting 60 s"); await asyncio.sleep(60)
        print("-- quota: 24 sequential tiny requests")
        first = None
        for i in range(1, 25):
            r = await call(c, "refusals", f"quota seq {i}", "POST", "/chat/completions", tiny())
            print(f"  seq {i}: {summarize(r)}")
            if r["status"] != 200 and first is None:
                first = i
        print(f"first refused request: {first}")
        print("-- waiting 90 s, then recovery check"); await asyncio.sleep(90)
        r = await call(c, "refusals", "recovery", "POST", "/chat/completions", tiny())
        print(f"  recovery: {summarize(r)}")


def tail(rec: dict, n: int = 70) -> tuple[str, int | None, str | None]:
    try:
        d = json.loads(rec["body"])
        return (d["choices"][0]["message"]["content"] or "")[-n:], d["usage"]["completion_tokens"], d["choices"][0]["finish_reason"]
    except (ValueError, KeyError, TypeError, IndexError):
        return "", None, None


def reset_epoch(rec: dict) -> float | None:
    from datetime import datetime, timezone
    m = re.search(r"Limit resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", rec.get("body") or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() if m else None


async def window() -> None:
    """A: fixed or sliding window? 20 requests (2 parallel), then 1 every 10 s until one succeeds."""
    comp = model(ModelClass.COMPACT)
    print("-- waiting 70 s"); await asyncio.sleep(70)
    sem = asyncio.Semaphore(2)
    t0m, t0e = time.monotonic(), time.time()

    async def one(i: int):
        async with sem:
            return i, time.monotonic() - t0m, await call(c, "window", f"burst {i}", "POST", "/chat/completions",
                                                         chat_body(comp, "Reply with: ok"))
    async with client() as c:
        res = await asyncio.gather(*[one(i) for i in range(1, 21)])
        bad = [(i, r["status"]) for i, _, r in res if r["status"] != 200]
        print(f"burst: 20 sent over {max(t for _, t, _ in res):.1f}s, non-200: {bad}")
        success_at = None
        for k in range(1, 15):
            await asyncio.sleep(max(0.0, 10 * k - (time.monotonic() - t0m)))
            r = await call(c, "window", f"probe T0+{10 * k}", "POST", "/chat/completions", chat_body(comp, "Reply with: ok"))
            off = time.monotonic() - t0m
            rs = reset_epoch(r)
            print(f"  T0+{off:5.1f}s status={r['status']} resets_at=T0+{(rs - t0e):.1f}s" if rs else
                  f"  T0+{off:5.1f}s status={r['status']}")
            if r["status"] == 200:
                success_at = off
                break
        if success_at:
            print("-- 5 rapid requests after the first success")
            for i in range(5):
                r = await call(c, "window", f"after-success {i}", "POST", "/chat/completions", chat_body(comp, "Reply with: ok"))
                rs = reset_epoch(r)
                print(f"  T0+{time.monotonic() - t0m:5.1f}s status={r['status']}" + (f" resets_at=T0+{rs - t0e:.1f}s" if rs else ""))


async def outlen() -> None:
    """B: which request controls shorten the output?"""
    comp, mid = model(ModelClass.COMPACT), model(ModelClass.MIDSIZE)
    ask = "Explain derivatives in detail."
    variants = [("stop=['.']", {"stop": ["."]}),
                ("max_completion_tokens=30", {"max_completion_tokens": 30}),
                ("num_predict=30 (top-level, as the SDK merges extra_body)", {"num_predict": 30}),
                ("options.num_predict=30 (top-level)", {"options": {"num_predict": 30}})]
    working = []
    async with client() as c:
        async def run(label, extra, mdl, cls):
            rec = await call(c, "outlen", f"{cls} {label}", "POST", "/chat/completions", chat_body(mdl, ask, **extra))
            t, ct, fin = tail(rec)
            ok = rec["status"] == 200 and ct is not None and ct <= 45
            print(f"{cls:7} {label}: status={rec['status']} completion_tokens={ct} finish={fin} -> {'WORKS' if ok else 'no effect'}"
                  + (f" body={rec['body'][:150]!r}" if rec["status"] != 200 else ""))
            return ok
        for label, extra in variants:
            if await run(label, extra, comp, "compact"):
                working.append((label, extra))
        if not any("num_predict" in l for l, _ in working):
            for label, extra in (("literal extra_body={'num_predict':30}", {"extra_body": {"num_predict": 30}}),
                                 ("literal extra_body={'options':{'num_predict':30}}", {"extra_body": {"options": {"num_predict": 30}}})):
                if await run(label, extra, comp, "compact"):
                    working.append((label, extra))
        rec = await call(c, "outlen", "compact json stop=['}']", "POST", "/chat/completions",
                         chat_body(comp, 'Return the JSON {"a":1,"b":"long text here..."} and nothing else', stop=["}"]))
        t, ct, fin = tail(rec, 90)
        print(f"compact JSON + stop=['}}']: status={rec['status']} completion_tokens={ct} finish={fin} tail={t!r} "
              f"contains_closing_brace={'}' in t}")
        for label, extra in working:
            await run(label, extra, mid, "midsize")


async def ceiling(cpt: float) -> None:
    """C: start- and end-anchored codewords at about 4,000 and 7,500 real prompt tokens."""
    await truncation(cpt, False, (4000, 7500), "ceiling")
    await truncation(cpt, True, (4000, 7500), "ceiling")


SAMPLES = {
    "Arabic (MSA)": "المشتقة هي معدل تغير الدالة عند نقطة معينة، وتُحسب باستخدام النهاية عندما يقترب التغير في المتغير من الصفر. ",
    "Darija (Arabic script)": "شنوا معناها المشتقة؟ ما فهمتش كيفاش نلقاها فالتمرين، عاوني شوية باش نفهم الفكرة ونعاود نحسب بوحدي. ",
    "Arabizi": "chnowa ya3ni derivee? ma fhemtch kifech nel9aha fel exercice, 3awenni chwaya bech nfahem el fekra w na3mel el 7sab wahdi. ",
}


async def cpt_probe() -> None:
    """D: chars per token for Arabic, Darija (Arabic script) and Arabizi, minus the empty-prompt baseline."""
    comp = model(ModelClass.COMPACT)
    async with client() as c:
        rec = await call(c, "chars_per_token", "baseline '.'", "POST", "/chat/completions", chat_body(comp, "."))
        base = json.loads(rec["body"])["usage"]["prompt_tokens"]
        print(f"baseline prompt_tokens for '.': {base}")
        for name, unit in SAMPLES.items():
            text = (unit * (1000 // len(unit) + 1))[:1000]
            rec = await call(c, "chars_per_token", name, "POST", "/chat/completions", chat_body(comp, text))
            pt = json.loads(rec["body"])["usage"]["prompt_tokens"]
            print(f"{name}: chars={len(text)} prompt_tokens={pt} -> {len(text) / (pt - base):.2f} chars/token "
                  f"(engine estimator assumes 2.00)")


def png(path: str, photo: bool) -> None:
    import random
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf", 110)
    img = Image.new("RGB", (900, 260), "white")
    ImageDraw.Draw(img).text((60, 60), "f'(x) = 6x + 5", font=font, fill=(20, 20, 60))
    if photo:
        img = img.rotate(3, expand=True, fillcolor=(235, 232, 220)).filter(ImageFilter.GaussianBlur(1.1))
        rnd, px = random.Random(1), img.load()
        for x in range(img.width):
            for y in range(img.height):
                r, g, b = px[x, y]
                n, shade = rnd.gauss(0, 14), 30 * x / img.width      # sensor noise + lighting gradient
                px[x, y] = tuple(max(0, min(255, int(v + n - shade))) for v in (r, g, b))
    img.save(path)


async def ocr(outdir: str) -> None:
    """E: deepseek-ocr with an OpenAI image_url (base64 data URL)."""
    import base64
    async with client() as c:
        for name, photo in (("clean", False), ("photo-like", True)):
            path = f"{outdir}/ocr_{name}.png"
            png(path, photo)
            url = "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
            body = {"model": "deepseek-ocr", "temperature": 0, "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Transcribe the math exactly."}, {"type": "image_url", "image_url": {"url": url}}]}]}
            rec = await call(c, "ocr", f"deepseek-ocr {name}", "POST", "/chat/completions", body, store_body=False)
            t, ct, fin = tail(rec, 300)
            print(f"{name}: status={rec['status']} {rec['elapsed_ms']:.0f}ms {summarize(rec)[:160]} transcription={t!r}"
                  + (f" ERROR={rec['body'][:300]!r}" if rec["status"] != 200 else ""))
        rec = await call(c, "ocr", "deepseek-ocr text only", "POST", "/chat/completions",
                         chat_body("deepseek-ocr", "Say hello in one word."))
        t, ct, fin = tail(rec, 200)
        print(f"text only: status={rec['status']} answer={t!r}" + (f" ERROR={rec['body'][:300]!r}" if rec["status"] != 200 else ""))


async def compare(name: str) -> None:
    """F: scripts.eval_classify through the engine with one compact model; adds completion tokens from the sink."""
    import scripts.eval_classify as ec
    from metrics import MemorySink
    made: list = []

    class Sink(MemorySink):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)
    ec.MemorySink = Sink
    sys.argv = ["eval_classify", "--model", name]
    await ec.main()
    chats = [r for r in made[0].records if r.kind == "chat" and r.outcome != "cache_hit"]
    for r in chats:
        d = r.model_dump() if hasattr(r, "model_dump") else dict(vars(r))
        with open(RAW, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "step": "compare", "label": name, "engine_record": d},
                               ensure_ascii=False, default=str) + "\n")
    ok = [r.tokens_out for r in chats if r.outcome == "ok" and r.tokens_out is not None]
    print(f"[{name}] provider chat attempts={len(chats)} outcomes={dict(__import__('collections').Counter(r.outcome for r in chats))} "
          f"mean completion tokens={sum(ok) / max(1, len(ok)):.1f} (n={len(ok)})")
    global SENT
    SENT += len(chats)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    run = {"models": models, "headers": headers, "maxtokens": maxtokens, "refusals": refusals,
           "window": window, "outlen": outlen, "chars": cpt_probe}.get(what)
    if what == "ceiling":
        asyncio.run(ceiling(float(sys.argv[2])))
    elif what == "ocr":
        asyncio.run(ocr(sys.argv[2]))
    elif what == "compare":
        asyncio.run(compare(sys.argv[2]))
    elif what == "truncation":
        asyncio.run(truncation(float(sys.argv[2]), len(sys.argv) > 3 and sys.argv[3] == 'end'))   # chars per real token, from the `headers` calibration
    elif run:
        asyncio.run(run())
    else:
        sys.exit(__doc__)
    print(f"requests sent by this invocation: {SENT}")
