"""
EdNova v0 — first end-to-end path.
  uvicorn app:app --port 8000      (mock: LLM_BASE_URL=http://localhost:8099/v1 in .env)
  open http://localhost:8000
POST /ask {"session": "...", "message": "..."} streams newline-delimited JSON events:
  lang -> triage -> status* -> hint -> status* -> answer   (or -> queued)
Flow per message: rules (lang) -> compact (triage) -> compact (hint, instant) -> tier (answer)
"""
import asyncio, json, os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from gateway import Gateway, GatewayError
from lang import detect, reply_plan
from triage import triage, choose_tier
from prompts import hint_messages, answer_messages, safe_hint

app = FastAPI()
gw = Gateway()

TEMPLATES_PATH = "darija_templates.json"
# Placeholder lines — Person B replaces these with real Tunisian Darija in darija_templates.json
FALLBACK_T = {
    "greet": {"darija": "Aslema! Chnowa t7eb tefhem lyoum?", "french": "Bonjour ! Qu'est-ce que tu veux comprendre aujourd'hui ?",
              "english": "Hi! What would you like to understand today?", "arabic": "مرحبا! ماذا تريد أن تفهم اليوم؟"},
    "hint_intro": {"darija": "Haw indice sghir 9bal el explication:", "french": "Un petit indice avant l'explication :",
                   "english": "A small hint before the explanation:", "arabic": "تلميح صغير قبل الشرح:"},
    "off_topic": {"darija": "Hedha barra el cours 😄 Nraj3ou lel maths walla python?", "french": "C'est hors sujet — on revient aux maths ou au Python ?",
                  "english": "That's off topic — back to math or Python?", "arabic": "هذا خارج الموضوع، نعود إلى الرياضيات أو بايثون؟"},
    "queued": {"darija": "El serveur m3abbi taw. 3awed ab3athli so2alek ba3d d9i9a.",
               "french": "Le serveur est très chargé en ce moment. Renvoie ta question dans une minute.",
               "english": "The server is very busy right now. Please resend your question in a minute.",
               "arabic": "الخادم مشغول جدا حاليا. أعد إرسال سؤالك بعد دقيقة."},
    "arabic_fallback": {"arabic": "النموذج العربي مشغول حاليا، إليك الشرح بالفرنسية:"},
    "fallback_notice": {"darija": "Explication mkhtasra 5ater el serveur m3abbi.", "french": "Version courte : le modèle détaillé est occupé.",
                        "english": "Short version: the detailed model is busy.", "arabic": "نسخة مختصرة لأن النموذج المفصل مشغول."},
}
T = {**FALLBACK_T, **(json.load(open(TEMPLATES_PATH, encoding="utf-8")) if os.path.exists(TEMPLATES_PATH) else {})}
LANG_NAME = {"french": "French", "english": "English", "arabic": "Modern Standard Arabic"}


def tpl(move, lang):
    v = T.get(move, {}).get(lang) or T.get(move, {}).get("french", "")
    return v[0] if isinstance(v, list) else v


class Ask(BaseModel):
    session: str
    message: str


@app.post("/ask")
async def ask(q: Ask):
    queue: asyncio.Queue = asyncio.Queue()

    def emit(kind, **data):
        queue.put_nowait(json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n")

    async def run():
        try:
            lang = detect(q.message)
            plan = reply_plan(lang)
            emit("lang", lang=lang, plan=plan)
            status = lambda s: emit("status", **s)

            t = await triage(gw, q.message, on_status=status)
            tier = choose_tier(t, lang)
            emit("triage", **t, tier=tier)

            if tier == "none":
                emit("answer", text=tpl("greet" if t["intent"] == "greeting" else "off_topic", plan["guidance"]),
                     model="template", tier="none", cached=False, fell_back=False)
                return

            out = plan["explanation"]
            if t["source"] == "default":             # platform unavailable: don't spend more calls
                emit("queued", text=tpl("queued", plan["guidance"]))
                return
            # 1) instant hint from compact (hints before answers + progress while mid works)
            try:
                hl = "french" if out == "arabic" else out          # compact writes poor Arabic
                h = await gw.call("compact", hint_messages(q.message, hl), max_tokens=60,
                                  temperature=0.3, on_status=status)
                emit("hint", intro=tpl("hint_intro", plan["guidance"]),
                     text=safe_hint(h.text, hl, t["concept"]), cached=h.cached)
            except GatewayError:
                pass

            # 2) the answer on the chosen tier (gateway falls back mid -> compact by itself)
            try:
                if out == "arabic":
                    # compact writes poor Arabic (recon): mid only, else French + notice
                    try:
                        r = await gw.call(tier, answer_messages(q.message, t, "arabic"),
                                          on_status=status, allow_fallback=False)
                        notice = ""
                    except GatewayError:
                        r = await gw.call("compact", answer_messages(q.message, t, "french", short=True),
                                          on_status=status)
                        r.fell_back = True
                        notice = tpl("arabic_fallback", "arabic")
                else:
                    r = await gw.call(tier, answer_messages(q.message, t, out, short=(tier == "compact")), on_status=status)
                    notice = tpl("fallback_notice", plan["guidance"]) if r.fell_back else ""
                emit("answer", text=r.text, model=r.model, tier=r.tier_used, cached=r.cached,
                     fell_back=r.fell_back, notice=notice)
            except GatewayError:
                emit("queued", text=tpl("queued", plan["guidance"]))
        except Exception as e:                       # never a frozen screen
            emit("error", text=str(e)[:200])
        finally:
            queue.put_nowait(None)

    async def stream():
        task = asyncio.create_task(run())
        while (item := await queue.get()) is not None:
            yield item
        await task

    return StreamingResponse(stream(), media_type="application/x-ndjson")


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>EdNova v0</title>
<style>body{font:16px system-ui;max-width:720px;margin:24px auto;padding:0 16px}
#log div{margin:6px 0;padding:8px 10px;border-radius:8px;background:#f3f3f6;white-space:pre-wrap}
.status{color:#777;font-size:13px;background:none!important;padding:0 10px!important}
.hint{background:#fff6dd!important}.answer{background:#e6f4ea!important}.queued{background:#fde8e8!important}
form{display:flex;gap:8px}input{flex:1;padding:10px;font:inherit}button{padding:10px 16px}</style>
<h2>EdNova v0</h2><div id=log></div>
<form id=f><input id=m placeholder="chnowa ya3ni dérivée ? / c'est quoi une limite ?" autofocus><button>Send</button></form>
<script>
const log=document.getElementById('log'),sid=Math.random().toString(36).slice(2,10);
const add=(c,t)=>{const d=document.createElement('div');d.className=c;d.textContent=t;log.appendChild(d);d.scrollIntoView()};
f.onsubmit=async e=>{e.preventDefault();const msg=m.value.trim();if(!msg)return;m.value='';add('me','🧑 '+msg);
 const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:sid,message:msg})});
 const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
 for(;;){const {done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
  let i;while((i=buf.indexOf('\\n'))>=0){const ev=JSON.parse(buf.slice(0,i));buf=buf.slice(i+1);
   if(ev.type==='status'){const s=ev.stage==='calling'?`⏳ ${ev.model} (${ev.tier})…`:ev.stage==='waiting_quota'?`⏸ quota, ${ev.seconds}s`:
      ev.stage==='model_busy'?`⏸ ${ev.model} busy, retry in ${ev.seconds}s`:ev.stage==='fallback'?`↘ fallback ${ev.from} → ${ev.to}`:ev.stage;add('status',s)}
   else if(ev.type==='lang')add('status',`🌐 ${ev.lang} → guidance: ${ev.plan.guidance}, explanation: ${ev.plan.explanation}`);
   else if(ev.type==='triage')add('status',`🧭 ${ev.intent} · ${ev.subject} · ${ev.concept||'-'} · route: ${ev.tier}`);
   else if(ev.type==='hint')add('hint','💡 '+ev.intro+' '+ev.text);
   else if(ev.type==='answer')add('answer',(ev.notice?ev.notice+'\\n':'')+ev.text+`\\n— ${ev.model}${ev.cached?' · cache':''}${ev.fell_back?' · fallback':''}`);
   else add(ev.type,ev.text)}}};
</script>"""


@app.get("/")
async def index():
    return HTMLResponse(PAGE)
