# Test scenarios on the real engine, FelCloud slot 2026-11-11

Cases from "Lumi test scenarios: high school and university" that need the real engine, run through the app server
(`app/dev.sh --real`) with the same endpoints and bodies the UI sends (`/api/ai/turn`, `/api/ai/run`,
`/api/ai/exercises/...`, `/api/materials`, `/api/ai/flags`). Screens were not opened: these results cover what the
engine and server return, not how the UI shows it. About 45 FelCloud requests; no server 429.

## Results

| ID | Result | What came back |
| --- | --- | --- |
| HS-04 | Pass | Read as ARABIZI; explain on mid-size with 2 course sources, 2 formulas verified, derivative 3x²+2. Body in French (known gap). |
| HS-05 | Pass | Bank exercise d05 (the bank has no x²(x+1)). Hints 1-3 one at a time, a 4th request repeats hint 3. "2x" INCORRECT, "2x+3" CORRECT (sympy); product mastery 0 → 0.24. |
| HS-06 | Pass | First wrong step = the last line. The guiding question is odd ("multipliée par 3x ou par x ?"); one compact call. |
| HS-07 | **Fail** (a), Pass (b) | a) "La dérivée de 1/x est 1/x²" → NOT_IN_COURSE (LLM), though the course says −1/x². b) PARTLY, split in two, by sympy. |
| HS-08 | **Fail**, then Pass after the fix | Legible synthetic image of f(x) = x³ − 2x → empty, `low_confidence` (Findings 1). With prompt v2: `f(x) = x^3 - 2x`, and a three-line solution read with one misread (`f'(1)` as `f(1)`). |
| HS-10 | Pass | "Not in the course", no formula. |
| HS-11 | Partial | Markdown notes indexed in 5 s (26 chunks); the answer cites the professor file but gives (vi+vf)/2, the constant-acceleration case, as "the general formula". Flagged `ungrounded_formula`; a derivative exercise is appended. |
| HS-12 | **Fail** | Classified QUIZ and served "Calculer la dérivée de f(x) = x⁴" instead of "not in the course". |
| HS-13 | Pass | Report → open flag; Reject → 1 cache entry removed; the re-ask is generated fresh (the new answer is still off-topic, see UNI-01). |
| UNI-01 | **Fail** | "The derivative of f at a point x is formally given by f'(x) = 2x − 4": retrieval returned the English variations page; the French definition chunks are not reached and the English definition draft is not indexed. |
| UNI-02 | Partial | Prerequisites first, weak concepts early. Pacing: days 1-3 have 40 min, day 4 has 100 min against 60 min/day, day 5 is empty. |
| UNI-03 | Pass | Split into two claims, both NOT_IN_COURSE by rules; the converse is never Supported. |
| UNI-04 | Partial | The second line is the first wrong step, but there is no note on the sign or the −3 numerator. |
| UNI-05 | Pass (engine) | With `class_id` the answer comes from the class-only PDF. Without it, the student was answered from the public scan (UNI-06), so the gap cannot be seen in this setup. The client never sends `class_id` (no occurrence in `app/client/src`), so the gap is real in the app. Answer correct (ad − bc) but flagged `ungrounded_formula`. |
| UNI-06 | Pass | Scanned PDF: 4 pages OCR'd, indexed in 105 s (21 chunks); the determinant answer cites page 4. |
| UNI-07 | Pass (text) | Binary search → "not in the course"; "Who won the match yesterday?" → OFF_TOPIC, no AI task. Voice not tested. |
| UNI-10 | **Fail** | Reply language English, question in French: the opener and exercise are English, the body is French. |
| UNI-11 | Pass (step 1) | "Not in the course". Prep and map not checked. |
| UNI-13 | Pass | With history the follow-up answers the quotient rule; after New chat it is answered as a new question. Both offer a product exercise. |
| X-05 | Pass | Second ask instant, `from_cache: true`. |

Not run: HS-03 and UNI-07 voice (needs people with microphones), X-01 on the real quota (would exhaust it on purpose),
HS-01 grading of the three level-test answers, and every screen-only check.

## Findings

1. **OCR prompt makes deepseek-ocr return nothing.** Same image (`f(x) = x³ − 2x`), four direct calls: "Transcribe the math
   exactly." returns `\[ f(x) = x^3 - 2x \]` with the image first or last; the `prompts/ocr_transcribe.md` v1 text
   returns `''` either way. Wording, not order. The scanned PDF still worked (dense printed pages).
   **Fixed the same day** (prompt v2, `DECISIONS.md` "OCR prompt v2"): verified through the app and by re-ingesting the scan.
2. **Every explain answer gets a derivative exercise**, including answers from physics and linear-algebra uploads
   (HS-11, UNI-05: "Calculer la dérivée de f(x) = x² + 9").
3. **Uploads are tagged with Analyse concepts at confidence 1**: the "Histoire" section of the speed article → `quotient`,
   "Matrice 2 × 2" → `produit`. 8 of 26 and 15 of 118 chunks pass `INGEST_MIN_TAG_CONFIDENCE` (0.6).
4. **Filler words fail the relevance gate.** "Je ne comprends pas la règle du produit u'v + uv', tu peux expliquer ?" →
   "not in the course": coverage 0.32 against 0.4, on the base index alone (reproduced offline; not caused by uploads).
   "C'est quoi la dérivée d'un produit ?" passes at 1.0.
5. **English questions miss the French course** (UNI-01): English chunks are searched first and any hit stops the
   search, so a weak English hit wins over the French definition.
6. **`ungrounded_formula` false positives on worked arithmetic** (`(2)(5) - (3)(4)`): the professor review queue fills
   with correct answers.
7. **check_claim reads `(1/x)' = …` but not "la dérivée de 1/x est …"** (HS-07a), while explain's checker does read
   that prose.

Raw results (one JSON per case) were kept in the session scratchpad, not in the repo.

## Replay after the fixes (same day, same slot)

Engine v1.5 (`DECISIONS.md` "Engine v1.5: the scenario findings"), through the app on the real engine, about 12 requests,
no server 429.

| ID | Result | What came back |
| --- | --- | --- |
| HS-07a | Pass | CONTRADICTED by sympy, "Plutôt : (1/x)' = -1/x²", no model call. |
| HS-12 | Pass | Classified QUIZ again; follow-up overridden to explain; "not in the course". |
| Finding 4 | Pass | The product rule with the course's example x(x + 1), then a product exercise. |
| UNI-01 | Pass | The limit of (f(a + h) − f(a)) / h in English from the French definition, not degraded. |
| UNI-10 | Pass | The quotient rule and the 1/(x + 2) example in English, not degraded. The exercise after it is a product one. |
| UNI-02 | Pass | 60, 40, 40, 40, 40 minutes over 5 days. |
| Findings 2, 3, 6 | Pass | Re-uploaded speed article and determinant chapter: 4 and 0 confident tags; answers without an exercise and without a flag. |

The first replay of UNI-01 and UNI-10 came back correct but flagged (`ungrounded_formula` on the limit definition,
`wrong_math` on "u is 1"): two checker false positives, fixed, then replayed with reworded questions because the
negative cache served the flagged ones in fast mode for 10 minutes, as designed.
