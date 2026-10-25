"""
BM25 tokenization for French, English, Arabic script (MSA + Darija) and Arabizi.

  1. NFKC (x² -> x2, presentation forms -> base letters), Arabic-Indic digits -> ASCII
  2. Arabic: strip diacritics (harakat, shadda, sukun, dagger alef) and tatweel; normalize
     alef variants (أ إ آ ٱ -> ا), yaa (ى ی -> ي), taa marbuta (ة -> ه)
  3. split on non-word characters (apostrophes too: l'exposant -> l, exposant)
  4. Latin-script tokens only: lowercase, fold accents (dérivée -> derivee: learners often type
     without accents), light plural and final-e strip (dérivées, dérivée, dérivé -> deriv). Arabizi digits are kept.
  5. Arabic-script tokens: light prefix strip of the article and attached particles
     (المشتقة / والمشتقة / بالمشتقة -> مشتقه), only if 3+ letters remain; a bare و only if 4+ remain
  6. drop 1-character tokens and stopwords (FR / EN / Arabizi / Arabic + Darija)
"""
from __future__ import annotations

import re
import unicodedata

_AR_LETTER = re.compile(r"[؀-ۿ]")
_AR_STRIP = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")   # diacritics + tatweel
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ی": "ي", "ة": "ه",
                         **{chr(0x0660 + i): str(i) for i in range(10)},
                         **{chr(0x06F0 + i): str(i) for i in range(10)}})
_WORD = re.compile(r"\w+")
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")


def strip_arabic_diacritics(text: str) -> str:
    return _AR_STRIP.sub("", text)


def normalize_arabic(text: str) -> str:
    return _AR_STRIP.sub("", text).translate(_AR_MAP)


def fold_latin(token: str) -> str:
    t = unicodedata.normalize("NFKD", token.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _latin(tok: str) -> str:
    t = fold_latin(tok)
    if len(t) > 4 and not any(c.isdigit() for c in t):
        if t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]                   # plural: derivees -> derivee
        while len(t) > 4 and t.endswith("e"):
            t = t[:-1]                   # French final e's: dérivée, dérivé (nombre dérivé) -> deriv
    return t


def _arabic(tok: str) -> str:
    for p in _AR_PREFIXES:
        if tok.startswith(p) and len(tok) - len(p) >= 3:
            return tok[len(p):]
    if tok.startswith("و") and len(tok) >= 5:      # bare conjunction: واشاره -> اشاره, but واحد stays
        return tok[1:]
    return tok


def _raw_tokens(text: str) -> list[str]:
    text = normalize_arabic(unicodedata.normalize("NFKC", text))
    out = []
    for tok in _WORD.findall(text):
        tok = _arabic(tok) if _AR_LETTER.search(tok) else _latin(tok)
        if len(tok) >= 2:
            out.append(tok)
    return out


_STOP_RAW = """
le la les un une des de du et ou est sont c ce cet cette ces que qui quoi qu comment pourquoi quand
je tu il elle on nous vous ils elles me te se en dans pour par sur avec sans pas ne plus mais au aux
y a son sa ses mon ma mes ton ta tes leur leurs tout tous tres bien fait faire peux peut dois
quel quelle quels quelles
the an of to in on for is are was be been what why how do does did can could i you he she it we they
my your this that these those and or with without please explain me tell about if its when
chnowa chnoua chnou chniya ya3ni yaani ena enti inti howa hiya fi fel el w ama ken kifech kifeh
3lech alech bech mta3 mte3 hedha hedhi chay barcha ki kifh nheb n7eb
من في على الى إلى عن ما ماذا هل هو هي هذا هذه ذلك التي الذي ان أن إن و ثم او أو كيف لماذا متى مع كل
شنوة شنوا شنية كيفاش علاش باش متاع برشا فما توا كي
معناها معنى يعني تعني معناه
"""
STOPWORDS = frozenset(t for t in _raw_tokens(_STOP_RAW))


def tokenize(text: str) -> list[str]:
    return [t for t in _raw_tokens(text) if t not in STOPWORDS]
