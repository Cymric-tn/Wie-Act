"""
Compact triage: ONE compact call per learner message -> routing decision.
Language is NOT asked of the model (lang.py does it with rules).
"""
from gateway import Gateway, GatewayError

SYSTEM = """You are a classifier for a tutoring app. Output ONE JSON object, nothing else.
Keys:
 "intent": one of "question", "answer_attempt", "confused", "greeting", "off_topic"
 "subject": one of "math", "python", "other"
 "concept": 1-4 words, in French
 "difficulty": one of "simple", "complex"
 "needs_reasoning": true only if a multi-step explanation, proof, plan or comparison is required
Rules:
 - A simple definition ("c'est quoi X", "chnowa X", "what is X", "ما هي X") -> difficulty "simple", needs_reasoning false.
 - "pourquoi", "démontre", "compare", "étape par étape", "plan", "3lech", debugging code -> difficulty "complex", needs_reasoning true.
 - The learner says they do not understand ("ma fhemtch", "je comprends rien") -> intent "confused".
Examples:
 "chnowa ya3ni limite?" -> {"intent":"question","subject":"math","concept":"limite","difficulty":"simple","needs_reasoning":false}
 "pourquoi la dérivée de ln(x) est 1/x ?" -> {"intent":"question","subject":"math","concept":"dérivée de ln","difficulty":"complex","needs_reasoning":true}
 "f'(x)=3 pour f(x)=3x+2 ?" -> {"intent":"answer_attempt","subject":"math","concept":"dérivée affine","difficulty":"simple","needs_reasoning":false}
Do NOT detect the language. Do NOT answer the question."""

INTENTS = {"question", "answer_attempt", "confused", "greeting", "off_topic"}
SUBJECTS = {"math", "python", "other"}
GREETINGS = {"salut", "bonjour", "hello", "hi", "aslema", "3aslema", "merci", "ok", "ok merci",
             "thanks", "yaatik saha", "ya3tik sa7a", "سلام", "عسلامة", "شكرا"}


def _rule_shortcut(msg: str):
    """Free answers for trivial messages — no model call, no quota."""
    t = msg.lower().strip(" !.?،")
    if t in GREETINGS or len(t) <= 3:
        return {"intent": "greeting", "subject": "other", "concept": "", "difficulty": "simple",
                "needs_reasoning": False, "source": "rules"}
    return None


def _validate(j: dict) -> dict:
    out = {
        "intent": j.get("intent") if j.get("intent") in INTENTS else "question",
        "subject": j.get("subject") if j.get("subject") in SUBJECTS else "other",
        "concept": str(j.get("concept") or "")[:60],
        "difficulty": j.get("difficulty") if j.get("difficulty") in ("simple", "complex") else "simple",
        "needs_reasoning": bool(j.get("needs_reasoning")),
        "source": "compact",
    }
    # confusion always deserves the careful explanation
    if out["intent"] == "confused":
        out["needs_reasoning"] = True
    return out


async def triage(gw: Gateway, msg: str, on_status=None) -> dict:
    if (r := _rule_shortcut(msg)):
        return r
    try:
        res = await gw.call("compact", [{"role": "system", "content": SYSTEM},
                                        {"role": "user", "content": msg}],
                            json_mode=True, max_tokens=80, temperature=0, on_status=on_status)
        return _validate(res.json())
    except (GatewayError, ValueError, KeyError):
        # triage unavailable or unparsable: safe default, never block the learner
        return {"intent": "question", "subject": "other", "concept": "", "difficulty": "simple",
                "needs_reasoning": False, "source": "default"}


def choose_tier(t: dict, lang: str) -> str:
    """The orchestration rule, in one place (goes in the report as the routing table)."""
    if t["intent"] in ("greeting", "off_topic"):
        return "none"                                   # templates only
    if lang == "arabic":
        return "mid"                                    # compact writes poor Arabic (recon)
    if t["subject"] == "python" and (t["needs_reasoning"] or t["difficulty"] == "complex"):
        return "code"
    if t["needs_reasoning"] or t["difficulty"] == "complex":
        return "mid"
    return "compact"
