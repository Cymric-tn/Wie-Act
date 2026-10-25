"""
Rule-based language detection — no LLM call (recon: no model reliably labels Tunisian Darija).
detect(text) -> "darija" | "arabic" | "french" | "english"
reply_plan(lang) -> which language each part of the answer uses.
"""
import re

AR_SCRIPT = re.compile(r"[؀-ۿ]")
# Tunisian markers in Arabic script (MSA rarely uses these)
DARIJA_AR = ["شنوة", "شنوا", "ما فهمتش", "فهمتش", "كيفاش", "علاش", "برشا", "توا", "باش", "فما",
             "موش", "ياسر", "شوية", "نحب", "كي ", "متاع", "هاذا", "هاذي", "ماو", "يعيشك"]
# Arabizi: digits used as letters inside words (3=ع, 7=ح, 9=ق, 5=خ, 2=ء)
ARABIZI_DIGIT = re.compile(r"\b[a-z]*[a-z][23579][a-z]+\b|\b[23579][a-z]{2,}\b", re.I)
DARIJA_LAT = {"chnowa", "chnoua", "chnou", "chniya", "kifech", "kifeh", "3lech", "alech", "barcha",
              "chwaya", "behi", "mouch", "moch", "fhemt", "fhemtch", "nchoufou", "ya3ni", "yaani",
              "hedha", "hedhi", "kima", "ena", "enti", "inti", "chay", "chey", "nheb", "n7eb", "taw",
              "bech", "fama", "famma", "mta3", "mte3", "fi", "fel", "lel", "3aweni", "3awenni",
              "w", "ama", "ken", "yesser", "barra", "aslema", "3aslema", "ya5i", "sa7a"}
FR = {"le", "la", "les", "un", "une", "des", "est", "et", "je", "tu", "que", "quoi", "pourquoi",
      "comment", "dans", "pour", "pas", "comprends", "c'est", "qu'est-ce", "fonction", "dérivée",
      "limite", "merci", "bonjour", "salut", "avec", "sur", "mais", "explique"}
EN = {"the", "is", "what", "why", "how", "a", "an", "and", "i", "you", "does", "do", "don't",
      "it", "of", "in", "to", "my", "this", "that", "explain", "please", "thanks", "hello"}
FR_ACCENT = re.compile(r"[éèêàùçôîâ]")


def detect(text: str) -> str:
    t = text.lower().strip()
    if not t:
        return "french"
    letters = [c for c in t if c.isalpha()]
    ar = sum(bool(AR_SCRIPT.match(c)) for c in letters)
    if letters and ar / len(letters) > 0.4:
        return "darija" if any(k in t + " " for k in DARIJA_AR) else "arabic"

    words = re.findall(r"[a-z0-9'éèêàùçôîâ]+", t)
    dar = sum(w in DARIJA_LAT for w in words) + 2 * len(ARABIZI_DIGIT.findall(t))
    fr = sum(w in FR for w in words) + (2 if FR_ACCENT.search(t) else 0)
    en = sum(w in EN for w in words)
    # one strong Darija signal outweighs French math vocabulary (code-switching is normal)
    if dar >= 2 or (dar >= 1 and dar >= fr):
        return "darija"
    if en > fr:
        return "english"
    return "french"


def reply_plan(lang: str) -> dict:
    """guidance = templates/short lines; explanation = generated content."""
    return {
        "darija":  {"guidance": "darija",  "explanation": "french"},   # no model writes Darija
        "arabic":  {"guidance": "arabic",  "explanation": "arabic"},   # arabic -> mid tier only
        "french":  {"guidance": "french",  "explanation": "french"},
        "english": {"guidance": "english", "explanation": "english"},
    }[lang]


if __name__ == "__main__":
    tests = [("chnowa ya3ni derivee?", "darija"), ("ma fhemt chay fel limites", "darija"),
             ("kifech na3mel liste fi python", "darija"), ("svp chnowa ya3ni limite quand x tend vers l'infini?", "darija"),
             ("ما فهمتش النهاية كي x يقرب للصفر", "darija"), ("شنوة الفرق بين المشتقة والأصلية؟", "darija"),
             ("ما هي المشتقة؟", "arabic"), ("ما الفرق بين المشتقة والدالة الأصلية؟", "arabic"),
             ("c'est quoi une dérivée ?", "french"), ("je comprends rien, c'est trop dur", "french"),
             ("f(x)=3x+2, f'(x)=3 ?", "french"), ("what is a for loop in python", "english"),
             ("why does my code print None?", "english"), ("salut !", "french"), ("ok merci", "french")]
    ok = 0
    for s, exp in tests:
        got = detect(s)
        ok += got == exp
        print(("ok  " if got == exp else "BAD ") + f"{got:8} <- {s}")
    print(f"{ok}/{len(tests)}")
