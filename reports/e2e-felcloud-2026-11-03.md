# End-to-end session through the app, 2026-11-03

Mode: **FelCloud (real engine)**, engine 1.4. Path: browser API → Node server → bridge → engine → provider. **8 provider requests** (budget 40); server 429s 0; stopped early: no.

| step | result | requests | seconds |
|---|---|---|---|
| 1. French explain (classify, then explain) | ok | 2 | 26.2 |
| 2. Arabizi explain | ok | 1 | 10.2 |
| 3. Exercise answered wrong, then right (mastery changes) | ok | 0 | 0.1 |
| 4. Board check with one wrong line | ok | 1 | 4.7 |
| 5. Compound fact-check | ok | 1 | 4.2 |
| 6. OCR read | ok | 1 | 12.8 |
| 7. One prep page | ok | 1 | 18.5 |
| 8. Upload, then an explain that uses it | ok | 1 | 8.8 |

## Findings

All eight steps ran, in order, through the app's HTTP API on the real engine: **8 provider requests of the 40 allowed, no server 429, nothing stopped early** (finished well before 23:45). The report was scanned for the key: 0 hits. Passing the script's checks is weaker than a good answer, so what the answers actually showed:

| step | what happened | verdict |
|---|---|---|
| 1 French explain | classify → `explain`, mid-size, 16.6 s, not degraded, grounded in two course pages, product rule stated correctly | works |
| 2 Arabizi explain | classified ARABIZI correctly, but the answer was "not in the course" in Arabizi (0 requests): the question did not retrieve any chunk | **retrieval miss**: the Arabizi phrasing ("derivee mta3 x²") does not reach the course; a glossary/expansion gap, not an app problem |
| 3 Exercise wrong, then right | `x³` → INCORRECT with the bank's mistake feedback; `4x³` → CORRECT; mastery 0 → 0.4 (2 attempts), graded by sympy, 0 requests; the solution never reached the browser | works |
| 4 Board check | one wrong step found at the right place (step 1 of 2), guiding question written by the compact model, but the question itself is poor ("Qu'est-ce qui se passe à la fonction quadratique dans le dérivé ?") | works, weak wording |
| 5 Compound fact-check | split into 2 parts: `(x²)' = 2x` SUPPORTED (sympy), the constant claim CONTRADICTED (compact), aggregate PARTLY with the right correction | works (v1.4 splitting live) |
| 6 OCR read | returned "Typewriter font." | **not a valid test**: my generated image uses Pillow's tiny default font, so the model read nothing; handwriting OCR quality is still unmeasured |
| 7 Prep page | plan built (0 requests), page for "nombre dérivé" generated, not degraded, but only **1** quiz item instead of 3 (the bank has few items for that concept) | works, thin content |
| 8 Upload then explain | the professor's upload was indexed (1 section) and **cited** (`professor/e2e-prof/…#asymptotes-obliques`), mid-size, 8.3 s; the answer was degraded `ungrounded_formula` because it added an invented example (`f(x) = 3x^2 + 2x`) with a claim about its asymptote | upload works end to end; the flag is plausible (the model went beyond the upload), the full answer was truncated in this report so the exact flagged span is not recorded |

Notes on the numbers:
- Step timings include the app's own overhead; the explain steps take 8–17 s on the 8B, as measured on Monday.
- The "Engine numbers" block at the end is **cumulative over the whole `metrics/calls.jsonl`** (301 provider requests, all of tonight's slot runs included), not this session; use the per-step request counts above.

## The slot runs of the same evening (`scripts/slot_run.sh`, reports `felcloud-2026-11-03-*`)

0 server 429s and 0 exceptions in every step. The 60-claim eval (43 claims decided by a model):

| | compact (3B) | mid-size (8B) |
|---|---|---|
| all 60 | 40 (66.7%) | **52 (86.7%)** |
| original 30 | 23 (76.7%) | 28 (93.3%) |
| added 30 | 17 (56.7%) | 24 (80.0%) |
| compound claims (24) | 12 (50%) | 21 (87.5%) |
| per part (40 parts) | 26 (65%) | 36 (90%) |
| p50 latency per model request | 3.4 s | 4.5 s |

Mixed load on the real API (cache off, 240 s, with per-request records saved, 343 lines): 65 interactive requests, p50 2.48 s, **p95 25.0 s**, 0 server 429s, densest window 19, 15 explains of which 4 hit the 25 s deadline (26.7%; Monday 17.6%); degraded: deadline 4, superseded 2, low_confidence 1, and **`quota_low` 0** (10 of 79 on Monday, the quota-aware wait). Not analysed yet: the records file.

### 1. French explain (classify, then explain)

```json
{
 "classified": {
  "intent": "EXPLAIN",
  "language": "FRENCH",
  "confusion": "LOW"
 },
 "task": "explain",
 "model_class": "midsize",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 16634.3,
 "events": [
  "retrieving",
  "checking_cache",
  "partial_result",
  "queued",
  "generating"
 ],
 "sources": [
  "derivee-erreurs.fr#erreurs-avec-le-produit",
  "derivee-formules.fr#somme-et-produit-par-une-constante"
 ],
 "answer": "Voici l'essentiel du cours sur ce point.\n\nLa dérivée d'un produit n'est pas le produit des dérivées. En effet, on utilise la formule (u·v)' = u'·v + u·v'. Par exemple, pour f(x) = x(x + 1), la dérivée est f'(x) = 2x + 1, ce qui est différent du produit des dérivées 1 × 1 = 1.\n\nÀ toi : Calculer la dérivée de f(x) = x(x + 3)."
}
```

### 2. Arabizi explain

```json
{
 "classified": {
  "intent": "EXPLAIN",
  "language": "ARABIZI",
  "confusion": "MEDIUM"
 },
 "follow": "explain",
 "task": "explain",
 "model_class": null,
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 0.5,
 "events": [
  "retrieving",
  "no_relevant_context"
 ],
 "answer": "Ma l9itech el sujet hedha fel cours eli 3andi. Tnajem t3awed tekteb el sou2el b tari9a okhra wala tes2el el prof?"
}
```

### 3. Exercise answered wrong, then right (mastery changes)

```json
{
 "exercise": "d01",
 "statement": "Calculer la dérivée de f(x) = x⁴.",
 "attempts": [
  "x³",
  "4x³"
 ],
 "wrong": {
  "verdict": "INCORRECT",
  "feedback": "Tu as baissé l'exposant mais oublié de multiplier par l'exposant.",
  "mastery": {
   "value": 0,
   "attempts": 1
  }
 },
 "right": {
  "verdict": "CORRECT",
  "mastery": {
   "value": 0.4,
   "attempts": 2
  }
 },
 "mastery_before": {},
 "graded_by": "sympy"
}
```

### 4. Board check with one wrong line

```json
{
 "task": "check_answer",
 "model_class": "compact",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 4705,
 "events": [
  "parsing_steps",
  "checking_steps",
  "queued",
  "generating",
  "answered_by_rules"
 ],
 "steps": [
  {
   "index": 0,
   "verdict": "OK",
   "note": ""
  },
  {
   "index": 1,
   "verdict": "WRONG",
   "note": "Cette ligne ne correspond pas à la précédente ni à la dérivée."
  }
 ],
 "first_wrong": 1,
 "feedback": "Qu'est-ce qui se passe à la fonction quadratique dans le dérivé ?",
 "graded_by": "llm"
}
```

### 5. Compound fact-check

```json
{
 "task": "check_claim",
 "model_class": "compact",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 4182.7,
 "events": [
  "claim_part",
  "answered_by_rules",
  "claim_part",
  "retrieving",
  "checking_cache",
  "queued",
  "generating"
 ],
 "verdict": "PARTLY",
 "correction": "La dérivée d'une constante est 0, pas 1 : une constante ne varie pas.",
 "parts": [
  {
   "claim": "(x²)' = 2x",
   "verdict": "SUPPORTED",
   "correction": "",
   "source_chunk_id": null,
   "checked_by": "sympy"
  },
  {
   "claim": "la dérivée d'une constante est 1",
   "verdict": "CONTRADICTED",
   "correction": "La dérivée d'une constante est 0, pas 1 : une constante ne varie pas.",
   "source_chunk_id": "derivee-erreurs.fr#oublier-que-la-derivee-d-une-constante-est-nulle",
   "checked_by": "llm"
  }
 ]
}
```

### 6. OCR read

```json
{
 "task": "ocr_transcribe",
 "model_class": "vision",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 12716.8,
 "events": [
  "checking_cache",
  "queued",
  "generating"
 ],
 "text": "Typewriter font.",
 "latex": null,
 "note": ""
}
```

### 7. One prep page

```json
{
 "plan_days": 5,
 "first_concept": "nombre dérivé",
 "title": "Nombre dérivé",
 "degraded": false,
 "degrade_reason": null,
 "from_cache": false,
 "quiz": 1,
 "explanation": "Voici l'essentiel du cours sur ce point.\n\nLe nombre dérivé de f en a, noté f'(a), est le nombre fini auquel tend le quotient (f(a + h) − f(a)) / h, lorsque h devient de plus en plus petit. C'est égal à 2a, par exemple, pour la fonction f(x) = x²."
}
```

### 8. Upload, then an explain that uses it

```json
{
 "sections": 1,
 "chunk_ids": [
  "professor/e2e-prof/mat-8c510205-23e#asymptotes-obliques"
 ],
 "task": "explain",
 "model_class": "midsize",
 "from_cache": false,
 "degraded": true,
 "degrade_reason": "ungrounded_formula",
 "latency_ms": 8255.1,
 "events": [
  "retrieving",
  "checking_cache",
  "partial_result",
  "queued",
  "generating",
  "guarded"
 ],
 "sources": [
  "professor/e2e-prof/mat-8c510205-23e#asymptotes-obliques"
 ],
 "answer": "Voici l'essentiel du cours sur ce point.\n\nUne asymptote oblique est une droite d'équation y = ax + b qui représente la limite de la courbe lorsque x tend vers l'infini. Cela signifie que la courbe se rapproche de cette droite sans la toucher. Nous la recherchons lorsque la fonction f(x) divisée par x tend vers un nombre a non nul. Par exemple, si f(x) = 3x^2 + 2x, alors l'asymptote oblique est la "
}
```

## Engine numbers for the session

```json
{
 "since": null,
 "filters": {
  "class_id": null,
  "course_id": null
 },
 "runs": {
  "total": 386,
  "by_task": {
   "check_answer": 5,
   "check_claim": 218,
   "classify": 86,
   "compress": 2,
   "explain": 19,
   "grade": 5,
   "hint": 44,
   "ocr_transcribe": 3,
   "prep_plan": 3,
   "quiz": 1
  }
 },
 "provider_requests": {
  "total": 301,
  "by_class": {
   "compact": 229,
   "midsize": 69,
   "vision": 3
  },
  "by_model": {
   "compact-a": 105,
   "deepseek-ocr": 3,
   "llama3.1-8b": 75,
   "llama3.1:8b": 12,
   "llama3.2-3b": 91,
   "llama3.2:3b": 15
  }
 },
 "cache": {
  "hits": 13,
  "runs": 386,
  "hit_rate": 0.0337
 },
 "degraded": {
  "count": 6,
  "share": 0.0155,
  "by_reason": {
   "midsize_saturated": {
    "count": 1,
    "share": 0.0026
   },
   "quota_low": {
    "count": 4,
    "share": 0.0104
   },
   "ungrounded_formula": {
    "count": 1,
    "share": 0.0026
   }
  }
 },
 "latency_ms": {
  "check_answer": {
   "count": 5,
   "p50": 1448.3,
   "p95": 4705.0
  },
  "check_claim": {
   "count": 218,
   "p50": 2220.9,
   "p95": 8810.7
  },
  "classify": {
   "count": 86,
   "p50": 1.3,
   "p95": 5900.9
  },
  "compress": {
   "count": 2,
   "p50": 2269.5,
   "p95": 5629.2
  },
  "explain": {
   "count": 19,
   "p50": 18482.8,
   "p95": 37081.1
  },
  "grade": {
   "count": 5,
   "p50": 4.6,
   "p95": 71.7
  },
  "hint": {
   "count": 44,
   "p50": 0.8,
   "p95": 6134.8
  },
  "ocr_transcribe": {
   "count": 3,
   "p50": 17064.7,
   "p95": 18624.1
  },
  "prep_plan": {
   "count": 3,
   "p50": 1.4,
   "p95": 3.0
  },
  "quiz": {
   "count": 1,
   "p50": 0.2,
   "p95": 0.2
  }
 },
 "server_429": {
  "count": 0,
  "by_outcome": {}
 }
}
```

## Round 2, 2026-11-03T20:58 (engine 1.4, 5 provider requests of 15, 429s 0, stopped: no)

| step | result | requests | seconds |
|---|---|---|---|
| R2.1 Arabizi explain: derivee mta3 x² | ok | 2 | 28.8 |
| R2.2 Arabizi explain: chnowa el derivee mta3 x² w kifech nal9aha ? | ok | 2 | 7.4 |
| R2.3 Upload, then the explain: full answer and flagged span | ok | 1 | 9.0 |

### R2.1 Arabizi explain: derivee mta3 x²

```json
{
 "question": "derivee mta3 x²",
 "classified": {
  "intent": "EXPLAIN",
  "language": "ARABIZI",
  "confusion": "MEDIUM"
 },
 "follow": "explain",
 "task": "explain",
 "model_class": "midsize",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 18255.6,
 "events": [
  "retrieving",
  "checking_cache",
  "partial_result",
  "queued",
  "generating"
 ],
 "sources": [
  "derivee-erreurs.fr#erreurs-avec-les-puissances",
  "derivee-formules.fr#derivees-des-fonctions-usuelles"
 ]
}
```

Full answer:

```text
Behi, n3awdouha bechwaya (el char7 bel français kima fel cours):

La dérivée de x² est 2x. Cela se déduit de la règle générale (xⁿ)' = n·xⁿ⁻¹. Ici, n = 2, donc (x²)' = 2·x²⁻¹ = 2x.

Jarreb enti : Calculer la dérivée de f(x) = x⁴.
```

### R2.2 Arabizi explain: chnowa el derivee mta3 x² w kifech nal9aha ?

```json
{
 "question": "chnowa el derivee mta3 x² w kifech nal9aha ?",
 "classified": {
  "intent": "HINT",
  "language": "ARABIZI",
  "confusion": "MEDIUM"
 },
 "follow": "hint",
 "task": "hint",
 "model_class": "compact",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 5789.8,
 "events": [
  "checking_cache",
  "queued",
  "generating"
 ],
 "sources": null
}
```

Full answer:

```text
Kifech kif 2x w 2x, msi x = 0 w 2x = 0?
```

### R2.3 Upload, then the explain: full answer and flagged span

```json
{
 "sections": 1,
 "task": "explain",
 "model_class": "midsize",
 "from_cache": false,
 "degraded": false,
 "degrade_reason": null,
 "latency_ms": 8501.8,
 "events": [
  "retrieving",
  "checking_cache",
  "partial_result",
  "queued",
  "generating"
 ],
 "sources": [
  "professor/e2e-prof/mat-70aa951b-78f#asymptotes-obliques"
 ],
 "flag_spans": {
  "new_examples": [
   "f(x) = x^2 + 2x"
  ],
  "formulas_verified": 0
 },
 "all_degrade_reasons": null,
 "notes_keys": [
  "budget",
  "dropped",
  "formulas_verified",
  "new_examples",
  "retrieved",
  "sources"
 ]
}
```

Full answer:

```text
Voici l'essentiel du cours sur ce point.

Une asymptote oblique est une droite de l'équation y = ax + b qui est tangente à la courbe de la fonction lorsque x tend vers l'infini. On la trouve en calculant la limite de f(x)/x lorsque x tend vers l'infini. Par exemple, si f(x) = x^2 + 2x, alors la limite de (x^2 + 2x)/x est égale à 2x + 2, ce qui implique qu'une asymptote oblique de la courbe est la droite y = 2x + 2.

À toi : Soit f(x) = x². Calculer f'(3).
```

Engine numbers for these calls (5 engine calls, by request id):

```json
{
 "since": null,
 "filters": {
  "class_id": null,
  "course_id": null
 },
 "runs": {
  "total": 5,
  "by_task": {
   "classify": 2,
   "explain": 2,
   "hint": 1
  }
 },
 "provider_requests": {
  "total": 5,
  "by_class": {
   "compact": 3,
   "midsize": 2
  },
  "by_model": {
   "llama3.1-8b": 2,
   "llama3.2-3b": 3
  }
 },
 "cache": {
  "hits": 0,
  "runs": 5,
  "hit_rate": 0.0
 },
 "degraded": {
  "count": 0,
  "share": 0.0,
  "by_reason": {}
 },
 "latency_ms": {
  "classify": {
   "count": 2,
   "p50": 1596.5,
   "p95": 10531.9
  },
  "explain": {
   "count": 2,
   "p50": 8501.8,
   "p95": 18255.6
  },
  "hint": {
   "count": 1,
   "p50": 5789.8,
   "p95": 5789.8
  }
 },
 "server_429": {
  "count": 0,
  "by_outcome": {}
 }
}
```

### Round 2 findings

5 provider requests of 15, no 429. Photo OCR and the check on its confirmed text (items 8) were **not run**: no file name was given; they run as soon as there is a photo (`scripts/e2e_round2.py --photo FILE`, then `--confirmed-text` once the learner has confirmed the reading).

- **Arabizi retrieval is fixed for "derivee mta3 x²"**: retrieved two course pages, mid-size explain in Arabizi framing with French content, 18 s, not degraded (R2.1).
- **The longer question still goes wrong, for another reason (R2.2):** retrieval is no longer the problem (the stopwords fix works offline, 6/6), but the classifier labelled "chnowa el derivee mta3 x² w kifech nal9aha ?" as **HINT** (language ARABIZI correct), so the engine answered with the hint task: a grounded-less, nonsensical hint ("Kifech kif 2x w 2x, msi x = 0 w 2x = 0?"), no sources. The follow-up rule routes HINT to `hint`; "kifech" ("how") plus a verb is read as "give me a hint". This is a classify (3B) weakness on Arabizi phrasing; a retrieval fix cannot help it. Candidate fixes (not made, they change the classifier): an Arabizi example in the classify prompt, or routing HINT without an open exercise or a problem text to `explain`.
- **Upload explain (R2.3), not flagged this time**, so the flagged span of the first run is not reproducible (the model answers differently each time; the first answer was not saved in full). What the answer shows instead: it cites the upload (`professor/e2e-prof/…#asymptotes-obliques`) and then adds an invented example with **wrong mathematics that the guard did not catch**: "si f(x) = x^2 + 2x, alors la limite de (x^2 + 2x)/x est égale à 2x + 2, ce qui implique qu'une asymptote oblique est y = 2x + 2" (the limit is infinite, f has no oblique asymptote). The formula check only judged `f(x) = x^2 + 2x` (a new example, `formulas_verified: 0`); a formula embedded in a sentence ("est égale à") is not an equation the extractor reads. This is a gap in the guard for prose-embedded claims, worth a decision.
