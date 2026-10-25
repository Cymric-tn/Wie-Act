"""
EdNova local mock of the platform contract (Technical Annex §5–§6).
Same paths, same status codes, same headers as the real service, so the gateway
is built and tested OFF-SLOT and the fault-injection demo uses identical code.

  pip install fastapi uvicorn httpx
  uvicorn mock_server:app --port 8099
  point the app at LLM_BASE_URL=http://localhost:8099/v1

Calibrate mock_config.json from recon_summary.json (latencies, ceilings,
header names, model ids). Optional: set "backend" to an Ollama/llama.cpp
OpenAI-compatible URL to get real text instead of canned text.

Fault injection (live, for tests and the demo video):
  POST /admin/fault {"mode": "none|quota|mid_saturated|ceiling|flaky", "prob": 1.0}
  GET  /admin/stats   -> per-model calls, tokens, refusals, repeated-identical count
"""
import asyncio, hashlib, json, os, random, time
from collections import Counter
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

CFG_PATH = os.environ.get("MOCK_CONFIG", "mock_config.json")
DEFAULT = {
    "models": {
        "compact-1.5b": {"class": "compact", "s_short": 1.5, "s_per_out_token": 0.03, "max_tokens": 2048},
        "mid-14b": {"class": "mid", "s_short": 6.0, "s_per_out_token": 0.12, "max_tokens": 2048},
    },
    "embed_model": "embed-small", "embed_dim": 384,
    "rpm": 20, "burst": 20, "max_concurrent": 2,          # gen bucket
    "embed_rpm": 60, "embed_burst": 60,                   # separate embeddings bucket
    "mid_slots": 1,                                        # mid-size saturates beyond this
    "headers": {"remaining": "x-ratelimit-remaining-requests",
                "limit": "x-ratelimit-limit-requests",
                "reset": "x-ratelimit-reset-requests"},
    "latency_scale": 1.0,                                  # 0.05 for fast unit tests
    "backend": None,                                       # e.g. "http://localhost:11434/v1"
    "backend_map": {"compact-1.5b": "qwen2.5:1.5b", "mid-14b": "qwen2.5:14b"},
}
CFG = {**DEFAULT, **(json.load(open(CFG_PATH)) if os.path.exists(CFG_PATH) else {})}


class Bucket:
    def __init__(self, rpm, burst):
        self.rate, self.cap, self.tokens, self.t = rpm / 60.0, burst, float(burst), time.time()

    def take(self):
        now = time.time()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate); self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True, 0.0
        return False, (1 - self.tokens) / self.rate


gen_bucket = Bucket(CFG["rpm"], CFG["burst"])
emb_bucket = Bucket(CFG["embed_rpm"], CFG["embed_burst"])
inflight = 0
mid_inflight = 0
fault = {"mode": "none", "prob": 1.0}
stats = {"calls": Counter(), "in_tok": Counter(), "out_tok": Counter(),
         "refusals": Counter(), "seen": Counter()}
app = FastAPI()


def qheaders(b):
    h = CFG["headers"]
    return {h["limit"]: str(int(b.cap)), h["remaining"]: str(int(b.tokens)),
            h["reset"]: f"{max(0.0, (b.cap - b.tokens) / b.rate):.1f}"}


def err(status, msg, typ, extra_headers=None):
    stats["refusals"][f"{status}:{typ}"] += 1
    return JSONResponse({"error": {"message": msg, "type": typ}}, status, headers=extra_headers or {})


def fault_hit(mode):
    return fault["mode"] == mode and random.random() < fault["prob"]


@app.get("/v1/models")
async def models():
    ids = list(CFG["models"]) + [CFG["embed_model"]]
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    global inflight, mid_inflight
    body = await req.json()
    model = body.get("model"); m = CFG["models"].get(model)
    if not m:
        return err(400, f"unknown model {model}", "invalid_request")
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    stats["seen"][key] += 1

    ok, wait = gen_bucket.take()
    if not ok or fault_hit("quota"):
        wait = max(wait, 5.0)
        return err(429, f"Team quota exhausted. Retry in {wait:.1f}s", "quota_exhausted",
                   {"Retry-After": f"{wait:.0f}", **qheaders(gen_bucket)})
    if inflight >= CFG["max_concurrent"]:
        return err(429, "Too many concurrent requests for this key", "concurrency_limit",
                   {"Retry-After": "2"})
    mt = body.get("max_tokens") or 256
    if mt > m["max_tokens"] or fault_hit("ceiling"):
        return err(400, f"max_tokens {mt} exceeds ceiling {m['max_tokens']}", "ceiling_exceeded")
    if m["class"] == "mid" and (mid_inflight >= CFG["mid_slots"] or fault_hit("mid_saturated")):
        return err(503, "Mid-size model class saturated", "model_saturated",
                   {"Retry-After": str(random.randint(5, 15))})
    if fault_hit("flaky"):
        return err(500, "upstream error", "server_error")
    if fault_hit("cooldown"):   # real LiteLLM behaviour observed in recon
        return err(429, f"No deployments available for selected model, Try again in 5 seconds. "
                        f"All deployments for selected model are in cooldown. Passed model={model}",
                   "all_deployments_in_cooldown", {"Retry-After": "5"})

    inflight += 1; mid_inflight += m["class"] == "mid"
    try:
        content, in_tok, out_tok = await generate(model, body, mt)
        await asyncio.sleep(CFG["latency_scale"] * (m["s_short"] + out_tok * m["s_per_out_token"]))
    finally:
        inflight -= 1; mid_inflight -= m["class"] == "mid"
    stats["calls"][model] += 1; stats["in_tok"][model] += in_tok; stats["out_tok"][model] += out_tok
    return JSONResponse({
        "id": "mock-" + key[:12], "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                  "total_tokens": in_tok + out_tok}}, headers=qheaders(gen_bucket))


async def generate(model, body, mt):
    prompt = " ".join(str(x.get("content", "")) for x in body.get("messages", []))
    in_tok = max(1, len(prompt) // 4)
    if CFG["backend"]:
        b = dict(body, model=CFG["backend_map"].get(model, model))
        async with httpx.AsyncClient(timeout=600) as c:
            r = (await c.post(CFG["backend"].rstrip("/") + "/chat/completions", json=b)).json()
        text = r["choices"][0]["message"]["content"]
        u = r.get("usage", {})
        return text, u.get("prompt_tokens", in_tok), u.get("completion_tokens", len(text) // 4)
    if body.get("response_format", {}).get("type") == "json_object" or "ONLY JSON" in prompt:
        text = json.dumps({"language": "french", "intent": "question", "concept": "mock",
                           "needs_reasoning": random.random() < 0.3})
    else:
        text = f"[mock {model}] " + "lorem ipsum " * min(mt // 4, 40)
    return text, in_tok, len(text) // 4


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    body = await req.json()
    ok, wait = emb_bucket.take()
    if not ok:
        return err(429, f"Embeddings quota exhausted. Retry in {wait:.1f}s", "quota_exhausted",
                   {"Retry-After": f"{max(wait, 1):.0f}", **qheaders(emb_bucket)})
    inputs = body.get("input"); inputs = [inputs] if isinstance(inputs, str) else inputs
    data = []
    for i, t in enumerate(inputs):
        rnd = random.Random(hashlib.md5(t.encode()).hexdigest())   # deterministic per text
        data.append({"object": "embedding", "index": i,
                     "embedding": [rnd.uniform(-1, 1) for _ in range(CFG["embed_dim"])]})
    stats["calls"]["embeddings"] += 1
    return JSONResponse({"object": "list", "data": data, "model": body.get("model"),
                         "usage": {"prompt_tokens": sum(len(t) // 4 for t in inputs)}},
                        headers=qheaders(emb_bucket))


@app.post("/admin/fault")
async def set_fault(req: Request):
    fault.update(await req.json()); return fault


@app.get("/admin/stats")
async def get_stats():
    compact = sum(v for k, v in stats["calls"].items() if CFG["models"].get(k, {}).get("class") == "compact")
    mid = sum(v for k, v in stats["calls"].items() if CFG["models"].get(k, {}).get("class") == "mid")
    return {"calls": stats["calls"], "in_tokens": stats["in_tok"], "out_tokens": stats["out_tok"],
            "compact_share": round(compact / max(1, compact + mid), 3),
            "repeated_identical": sum(c - 1 for c in stats["seen"].values() if c > 1),
            "refusals": stats["refusals"], "fault": fault}
