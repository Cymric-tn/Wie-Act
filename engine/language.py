"""
Rule-based language signals: zero LLM calls. Ported from the legacy lang.py / prompts.py.

  detect(text)              -> (label, strong)   label in LANGUAGES
  reconcile(model, text)    -> (label, source)   correct classify's language where rules are certain
  drifted(reply, language)  -> bool              reply not in the expected language (hint guard)

Recon showed no model reliably labels Tunisian Darija, so script-level facts (Arabic vs Latin
letters, Arabizi digits) override the model; within a script the model decides unless a dialect
marker is unambiguous.
"""
from __future__ import annotations

import re

LANGUAGES = ("DARIJA", "ARABIZI", "FRENCH", "ARABIC", "ENGLISH")
ARABIC_SCRIPT = ("DARIJA", "ARABIC")

_AR = re.compile(r"[؀-ۿ]")
# Tunisian markers in Arabic script (MSA rarely uses these)
_DARIJA_AR = ["شنوة", "شنوا", "شنية", "ما فهمتش", "فهمتش", "كيفاش", "علاش", "برشا", "توا", "باش", "فما",
              "موش", "ياسر", "شوية", "نحب", "كي ", "متاع", "هاذا", "هاذي", "ماو", "يعيشك", "قداش", "وقتاش",
              "ياخي", "زادة", "هكا", "تنجم", "نجمش", "عطيني"]
# Arabizi: digits used as letters inside words (3=ع, 7=ح, 9=ق, 5=خ, 2=ء)
_ARABIZI_DIGIT = re.compile(r"\b[a-z]*[a-z][23579][a-z]+\b|\b[23579][a-z]{2,}\b", re.I)
_DARIJA_LAT = {"chnowa", "chnoua", "chnou", "chniya", "kifech", "kifeh", "3lech", "alech", "barcha",
               "chwaya", "behi", "mouch", "moch", "fhemt", "fhemtch", "nchoufou", "ya3ni", "yaani",
               "hedha", "hedhi", "kima", "ena", "enti", "inti", "chay", "chey", "nheb", "n7eb", "taw",
               "bech", "fama", "famma", "mta3", "mte3", "fel", "lel", "3aweni", "3awenni",
               "ama", "ken", "yesser", "barra", "aslema", "3aslema", "ya5i", "sa7a", "a3tini", "3tini"}
_FR = {"le", "la", "les", "un", "une", "des", "est", "et", "je", "tu", "que", "quoi", "pourquoi",
       "comment", "dans", "pour", "pas", "comprends", "c'est", "qu'est-ce", "fonction", "dérivée",
       "limite", "merci", "bonjour", "salut", "avec", "sur", "mais", "explique", "moi", "peux", "j'ai",
       "résoudre", "calculer", "trouver", "montrer", "équation"}
_EN = {"the", "is", "what", "why", "how", "a", "an", "and", "i", "you", "does", "do", "don't",
       "it", "of", "in", "to", "my", "this", "that", "explain", "please", "thanks", "hello", "can",
       "solve", "find", "calculate", "compute", "show", "prove", "equation"}
_FR_ACCENT = re.compile(r"[éèêàùçôîâ]")


def normalize_label(v: object) -> str | None:
    s = str(v or "").strip().upper()
    return s if s in LANGUAGES else None


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(bool(_AR.match(c)) for c in letters) / len(letters) if letters else 0.0


def _latin_scores(t: str) -> tuple[int, int, int]:
    words = re.findall(r"[a-z0-9'éèêàùçôîâ]+", t)
    dar = sum(w in _DARIJA_LAT for w in words) + 2 * len(_ARABIZI_DIGIT.findall(t))
    fr = sum(w in _FR for w in words) + (2 if _FR_ACCENT.search(t) else 0)
    en = sum(w in _EN for w in words)
    return dar, fr, en


def detect(text: str) -> tuple[str, bool]:
    """(label, strong). strong = the rules are confident enough to override a model."""
    t = text.lower().strip()
    if not t:
        return "FRENCH", False
    if arabic_ratio(t) > 0.4:
        if any(k in t + " " for k in _DARIJA_AR):
            return "DARIJA", True
        return "ARABIC", False                      # MSA vs unmarked dialect: let the model decide
    dar, fr, en = _latin_scores(t)
    # one strong Darija signal outweighs French math vocabulary (code-switching is normal)
    if dar >= 2:
        return "ARABIZI", True
    if dar >= 1 and dar >= fr:
        return "ARABIZI", False
    if en > fr:
        return "ENGLISH", en >= 2 and fr == 0
    return "FRENCH", fr >= 2 and en == 0


def reconcile(model_label: str | None, text: str) -> tuple[str, str]:
    """Final language and its source ("model" | "rules")."""
    rule, strong = detect(text)
    arabic_text = arabic_ratio(text.lower()) > 0.4
    if model_label is None:
        return rule, "rules"
    if arabic_text != (model_label in ARABIC_SCRIPT):  # script mismatch: the model is certainly wrong
        return rule, "rules"
    if strong and rule != model_label:
        return rule, "rules"
    return model_label, "model"


# ------------------------------------------------------------------ reply drift guard
_FOREIGN = {
    "FRENCH": re.compile(r"\b(você|relação|derivada|poderia|função|não|taxa|uma|é uma|"
                         r"the|what|which|is the|derivative|function|qué|cómo|también)\b", re.I),
    "ENGLISH": re.compile(r"\b(est|une|des|dérivée|fonction|pourquoi|você|não|qué)\b", re.I),
}


def drifted(text: str, language: str) -> bool:
    if not text.strip():
        return True
    ratio = arabic_ratio(text)
    if language in ARABIC_SCRIPT:
        return ratio < 0.5
    if ratio > 0.2:                                  # Arabic script in a Latin-script reply
        return True
    pat = _FOREIGN.get(language)
    return bool(pat and len(pat.findall(text)) >= 2)


# ------------------------------------------------------------------ language contamination (Arabic-script text)
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
MATH_NOTATION = frozenset({"sin", "cos", "tan", "ln", "log", "exp", "lim", "max", "min", "sqrt", "dx", "dy", "inf"})


def _fold(w: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", w.lower()) if not unicodedata.combining(c))


def latin_words(text: str) -> set[str]:
    return {_fold(w) for w in _LATIN_WORD.findall(text)}


def contamination(text: str, allowed: set[str]) -> list[str]:
    """Latin words inside Arabic-script lines that are not math notation (1-2 letters, sin, lim...),
    not an allowed word (French course terms, words of the formulas/material the answer is based on).
    Catches small-model slips such as "maka" in the middle of an Arabic sentence."""
    bad: list[str] = []
    for line in text.splitlines():
        if arabic_ratio(line) < 0.5:
            continue
        for w in _LATIN_WORD.findall(line):
            f = _fold(w)
            if len(f) <= 2 or f in MATH_NOTATION or f in allowed or w in bad:
                continue
            bad.append(w)
    return bad


# ------------------------------------------------------------------ explicit non-understanding (confusion floor)
_LOST = re.compile(
    r"\bma\s*fhem(t|tch|tech|tich)\b|\bmafhem(t|tch|tech)\b|ما\s*فهمت|مافهمت|لم\s*أفهم|لا\s*أفهم|"
    r"\bje\s+(ne\s+)?comprends\s+(pas|rien)\b|\bj'ai\s+rien\s+compris\b|\bj'ai\s+pas\s+compris\b|"
    r"\bi\s+(don'?t|do\s+not)\s+(understand|get\s+it)\b", re.I)


def says_lost(text: str) -> bool:
    """The learner explicitly says they do not understand (any of the 5 languages)."""
    return bool(_LOST.search(text))
