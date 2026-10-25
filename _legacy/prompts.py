"""
Prompts written IN the output language (small models drift less when the instructions match the
reply language) + a drift guard that replaces a drifted hint by a template.
"""
import re

AR = re.compile(r"[؀-ۿ]")

# ------------------------------------------------------------------ hint (compact)
HINT_SYS = {
    "french": ("Tu es un tuteur patient. Tu ne donnes JAMAIS la réponse ni la définition. "
               "Tu écris UNE seule question courte (max 20 mots) qui fait réfléchir l'élève. "
               "Réponds uniquement en français."),
    "english": ("You are a patient tutor. NEVER give the answer or the definition. "
                "Write ONE short question (max 20 words) that makes the learner think. Reply in English only."),
    "arabic": ("أنت معلم صبور. لا تعطِ الجواب أو التعريف أبدا. اكتب سؤالا واحدا قصيرا (20 كلمة على الأكثر) "
               "يجعل التلميذ يفكر. أجب بالعربية الفصحى فقط."),
}
HINT_SHOTS = {
    "french": [("c'est quoi une limite ?",
                "Que se passe-t-il pour 1/x quand x devient très grand ?"),
               ("pourquoi la dérivée de x² est 2x ?",
                "Que vaut la pente entre x et x+h pour x², et que devient-elle quand h tend vers 0 ?")],
    "english": [("what is a limit?", "What happens to 1/x when x gets very large?")],
    "arabic": [("ما هي النهاية؟", "ماذا يحدث لـ 1/x عندما تصبح x كبيرة جدا؟")],
}
HINT_FALLBACK = {
    "french": "Avant l'explication : qu'est-ce que tu sais déjà sur « {c} » ?",
    "english": "Before the explanation: what do you already know about \"{c}\"?",
    "arabic": "قبل الشرح: ماذا تعرف مسبقا عن «{c}»؟",
}


def hint_messages(msg, lang):
    m = [{"role": "system", "content": HINT_SYS[lang]}]
    for q, a in HINT_SHOTS[lang]:
        m += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return m + [{"role": "user", "content": msg}]


# ------------------------------------------------------------------ answer (compact or mid)
ANS = {
    "french": {
        "role": "Tu es un tuteur pour des élèves et étudiants tunisiens. Réponds uniquement en français.",
        "explain": "Explique la notion",
        "check": "Vérifie la réponse de l'élève : dis si elle est juste, explique l'erreur s'il y en a une, puis donne le bon raisonnement",
        "short": "en 3 phrases simples",
        "long": "étape par étape, avec un exemple concret, en 150 mots maximum",
        "confused": " L'élève est perdu : pars des bases, avec des mots simples.",
        "end": " Pas d'introduction ni de formule de politesse. Termine par UNE question courte pour vérifier la compréhension.",
        "notation": " Notation simple : x^2, f'(x).",
    },
    "english": {
        "role": "You are a tutor for Tunisian students. Reply in English only.",
        "explain": "Explain the concept",
        "check": "Check the learner's answer: say if it is correct, explain the mistake if any, then give the correct reasoning",
        "short": "in 3 simple sentences",
        "long": "step by step, with one concrete example, max 150 words",
        "confused": " The learner is lost: start from the basics, in simple words.",
        "end": " No preamble. End with ONE short question that checks understanding.",
        "notation": " Simple notation: x^2, f'(x).",
    },
    "arabic": {
        "role": "أنت معلم لتلاميذ وطلبة تونسيين. أجب بالعربية الفصحى البسيطة فقط، واحتفظ بالرموز الرياضية كما هي.",
        "explain": "اشرح المفهوم",
        "check": "تحقق من جواب التلميذ: قل إن كان صحيحا، واشرح الخطأ إن وجد، ثم قدم الاستدلال الصحيح",
        "short": "في 3 جمل بسيطة",
        "long": "خطوة بخطوة، مع مثال ملموس، في 150 كلمة على الأكثر",
        "confused": " التلميذ تائه: ابدأ من الأساسيات بكلمات بسيطة.",
        "end": " بدون مقدمة. اختم بسؤال واحد قصير للتحقق من الفهم.",
        "notation": " رموز بسيطة: x^2 و f'(x).",
    },
}


def answer_messages(msg, t, lang, short=False):
    a = ANS[lang]
    task = a["check"] if t["intent"] == "answer_attempt" else a["explain"]
    sys = (f"{a['role']} {task} {a['short'] if short else a['long']}."
           f"{a['confused'] if t['intent'] == 'confused' else ''}{a['end']}{a['notation']}")
    return [{"role": "system", "content": sys}, {"role": "user", "content": msg}]


# ------------------------------------------------------------------ drift guard
FOREIGN = {
    "french": re.compile(r"\b(você|relação|derivada|poderia|função|não|taxa|uma|é uma|"
                         r"the|what|which|is the|derivative|function|qué|cómo|también)\b", re.I),
    "english": re.compile(r"\b(est|une|des|dérivée|fonction|pourquoi|você|não|qué)\b", re.I),
}


def drifted(text: str, lang: str) -> bool:
    if not text.strip():
        return True
    if lang == "arabic":
        letters = [c for c in text if c.isalpha()]
        return not letters or sum(bool(AR.match(c)) for c in letters) / len(letters) < 0.5
    pat = FOREIGN.get(lang)
    return bool(pat and len(pat.findall(text)) >= 2)


def safe_hint(text, lang, concept):
    return HINT_FALLBACK[lang].format(c=concept or "ce sujet") if drifted(text, lang) else text.strip()


if __name__ == "__main__":
    bad = ("La dérivée d'une fonction représente la vitesse moyenne. Quelle relação você poderia "
           "estabelecer entre a derivada e a taxa de variação de uma função?")
    good = "Que se passe-t-il pour la pente de la courbe quand on zoome sur un point ?"
    print("bad drifted:", drifted(bad, "french"), "| good drifted:", drifted(good, "french"))
    print(safe_hint(bad, "french", "dérivée"))
