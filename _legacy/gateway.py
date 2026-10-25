"""
EdNova LLM gateway — the single door to the platform. Every model call goes through here.

Implements what recon (22 Oct) observed on the real platform (LiteLLM proxy):
  * 20 req/min per key as a per-minute window  -> own sliding window (18/min, headroom)
  * 2 parallel requests per key                 -> semaphore(2)
  * 429 all_deployments_in_cooldown = saturation -> circuit breaker: skip that model until Retry-After,
                                                   fall back mid -> compact; if all cool briefly, wait once
  * 429 quota                                    -> wait Retry-After, retry same tier
  * 90 s server timeout                          -> per-tier max_tokens caps + 85 s client timeout
  * errors put models in cooldown               -> validate input size before sending
  * exact-hash cache                             -> organizers' "repeated identical requests" indicator
Config from env only (.env):  LLM_BASE_URL, LLM_API_KEY
Usage:
    gw = Gateway()
    r = await gw.call("mid", messages, json_mode=False, on_status=print)
    r.text, r.model, r.tier_used, r.cached, r.fell_back
"""
import asyncio, hashlib, json, os, re, sqlite3, time
from collections import deque
from dataclasses import dataclass, field
import httpx
from dotenv import load_dotenv

load_dotenv()

TIERS = {                                   # ordered model preference per tier
    "compact": ["qwen2.5-3b", "llama3.2-3b"],
    "mid": ["llama3.1-8b"],
    "code": ["qwen2.5-coder-7b", "llama3.1-8b"],
}
FALLBACK = {"mid": "compact", "code": "mid", "compact": None}
MAX_TOKENS = {"compact": 300, "mid": 550, "code": 550}
BLOCKLIST = {"deepseek-ocr", "mistral-7b"}
INPUT_TOKEN_BUDGET = 1800                   # recon: input may be truncated near 2048
RPM = 18
MAX_PARALLEL = 2
CLIENT_TIMEOUT = 85
MAX_WAIT = 30                               # never make a user wait longer than this on one retry


class GatewayError(Exception):
    pass


@dataclass
class Result:
    text: str
    model: str
    tier_used: str
    cached: bool = False
    fell_back: bool = False
    attempts: int = 1
    latency_s: float = 0.0
    usage: dict = field(default_factory=dict)

    def json(self):
        t = self.text.strip()
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)          # coder-7b wraps JSON in fences
        m = re.search(r"\{.*\}", t, re.S)                      # mistral-style trailing prose
        return json.loads(m.group(0) if m else t)


class SlidingWindow:
    def __init__(self, rpm):
        self.rpm, self.ts, self.lock = rpm, deque(), asyncio.Lock()
        self.blocked_until = 0.0

    async def acquire(self, on_status=None):
        while True:
            async with self.lock:
                now = time.time()
                while self.ts and now - self.ts[0] >= 60:
                    self.ts.popleft()
                if now >= self.blocked_until and len(self.ts) < self.rpm:
                    self.ts.append(now)
                    return
                wait = max(self.blocked_until - now, 60 - (now - self.ts[0]) if self.ts else 1)
            if on_status:
                on_status({"stage": "waiting_quota", "seconds": round(wait, 1)})
            await asyncio.sleep(min(wait, 5))

    def block_for(self, seconds):
        self.blocked_until = max(self.blocked_until, time.time() + seconds)


class Cache:
    def __init__(self, path="cache.sqlite"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT, ts REAL)")

    @staticmethod
    def key(tier, messages, max_tokens, json_mode):
        norm = [{"role": m["role"], "content": " ".join(m["content"].split()).lower()} for m in messages]
        raw = json.dumps([tier, norm, max_tokens, json_mode], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, k):
        row = self.db.execute("SELECT v FROM c WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO c VALUES (?,?,?)", (k, json.dumps(v), time.time()))
        self.db.commit()


class Gateway:
    def __init__(self, metrics_path="metrics.jsonl", cache_path="cache.sqlite"):
        self.base = os.environ["LLM_BASE_URL"].rstrip("/")
        self.key = os.environ["LLM_API_KEY"]
        self.client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
        self.window = SlidingWindow(RPM)
        self.sem = asyncio.Semaphore(MAX_PARALLEL)
        self.cache = Cache(cache_path)
        self.metrics = open(metrics_path, "a", encoding="utf-8")
        self.cooldown = {}          # circuit breaker: model -> time it may be called again

    # ------------------------------------------------------------ public
    async def call(self, tier, messages, json_mode=False, max_tokens=None,
                   temperature=0.2, on_status=None, use_cache=True, allow_fallback=True):
        if tier not in TIERS:
            raise GatewayError(f"unknown tier {tier}")
        est = sum(len(m["content"]) for m in messages) // 4
        if est > INPUT_TOKEN_BUDGET:
            raise GatewayError(f"input ~{est} tokens > budget {INPUT_TOKEN_BUDGET}; narrow the context first")
        max_tokens = min(max_tokens or MAX_TOKENS[tier], MAX_TOKENS[tier])

        k = Cache.key(tier, messages, max_tokens, json_mode)
        if use_cache and (hit := self.cache.get(k)):
            self._log(tier, hit["model"], "cache", 0, {}, cached=True)
            return Result(hit["text"], hit["model"], tier, cached=True)

        attempts = 0
        for rnd in range(2):                      # round 2 only if every model is briefly cooling down
            current, fell_back = tier, False
            while current:
                for model in TIERS[current]:
                    if model in BLOCKLIST:
                        continue
                    if self.cooldown.get(model, 0) > time.time():
                        if on_status:
                            on_status({"stage": "skip_cooling", "model": model})
                        continue
                    r = await self._try_model(current, model, messages, json_mode, max_tokens,
                                              temperature, on_status)
                    attempts += r.pop("attempts")
                    if r["ok"]:
                        res = Result(r["text"], model, current, fell_back=fell_back,
                                     attempts=attempts, latency_s=r["lat"], usage=r["usage"])
                        if use_cache:
                            self.cache.put(k, {"text": res.text, "model": model})
                        return res
                nxt = FALLBACK[current] if allow_fallback else None
                if nxt and on_status:
                    on_status({"stage": "fallback", "from": current, "to": nxt})
                current, fell_back = nxt, True
            chain = self._chain(tier) if allow_fallback else [m for m in TIERS[tier] if m not in BLOCKLIST]
            waits = [self.cooldown.get(m, 0) - time.time() for m in chain]
            all_cooling = bool(waits) and all(w > 0 for w in waits)
            soonest = min(waits) if all_cooling else 99
            if rnd == 0 and all_cooling and soonest <= 10:
                if on_status:
                    on_status({"stage": "all_busy_waiting", "seconds": round(soonest, 1)})
                await asyncio.sleep(soonest)
                continue
            break
        raise GatewayError("all tiers unavailable — queue the request and tell the user")

    # ------------------------------------------------------------ internals
    async def _try_model(self, tier, model, messages, json_mode, max_tokens, temperature, on_status):
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        attempts = 0
        for _ in range(3):
            attempts += 1
            await self.window.acquire(on_status)
            async with self.sem:
                if on_status:
                    on_status({"stage": "calling", "tier": tier, "model": model})
                t0 = time.time()
                try:
                    resp = await self.client.post(f"{self.base}/chat/completions", json=body,
                                                  headers={"Authorization": f"Bearer {self.key}"})
                except httpx.TimeoutException:
                    self._log(tier, model, "timeout", time.time() - t0, {})
                    return {"ok": False, "attempts": attempts}
                except httpx.HTTPError as e:
                    self._log(tier, model, f"neterr:{type(e).__name__}", time.time() - t0, {})
                    await asyncio.sleep(2)
                    continue
            lat = time.time() - t0
            self._read_headers(resp.headers)
            data = self._safe_json(resp)
            etype = (data.get("error") or {}).get("type", "") if isinstance(data, dict) else ""
            self._log(tier, model, resp.status_code, lat, data.get("usage") or {}, etype=etype)

            if resp.status_code == 200:
                text = data["choices"][0]["message"]["content"] or ""
                return {"ok": True, "text": text, "lat": lat, "usage": data.get("usage") or {},
                        "attempts": attempts}
            ra = self._retry_after(resp)
            if resp.status_code in (429, 503) and ("cooldown" in etype or resp.status_code == 503):
                # model saturated: open the breaker for this model, let the caller try the next one
                self.cooldown[model] = time.time() + min(ra, 60)
                if on_status:
                    on_status({"stage": "model_busy", "model": model, "seconds": ra})
                return {"ok": False, "attempts": attempts}
            if resp.status_code == 429:
                # key quota: wait and retry same model
                self.window.block_for(ra)
                if ra > MAX_WAIT:
                    return {"ok": False, "attempts": attempts}
                continue
            if resp.status_code == 400:
                raise GatewayError(f"400 from platform (fix the request): {data}")
            if resp.status_code >= 500:
                await asyncio.sleep(2)
                continue
            return {"ok": False, "attempts": attempts}      # 401/404 etc: don't hammer
        return {"ok": False, "attempts": attempts}

    @staticmethod
    def _chain(tier):
        seen, t = [], tier
        while t:
            seen += [m for m in TIERS[t] if m not in BLOCKLIST and m not in seen]
            t = FALLBACK[t]
        return seen

    def _read_headers(self, h):
        rem = h.get("x-ratelimit-api_key-remaining-requests")
        if rem is not None and rem.isdigit() and int(rem) == 0:
            self.window.block_for(60)

    @staticmethod
    def _retry_after(resp):
        try:
            return float(resp.headers.get("retry-after", 5))
        except ValueError:
            return 5.0

    @staticmethod
    def _safe_json(resp):
        try:
            return resp.json()
        except ValueError:
            return {}

    def _log(self, tier, model, status, lat, usage, cached=False, etype=""):
        self.metrics.write(json.dumps({
            "ts": time.time(), "tier": tier, "model": model, "status": status,
            "lat": round(lat, 2), "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
            "cached": cached, "etype": etype}) + "\n")
        self.metrics.flush()

    async def aclose(self):
        await self.client.aclose()
