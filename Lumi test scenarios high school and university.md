# Lumi test scenarios: high school and university

Nov 10, 2026 · @Ibrahim

36 test cases cover high-school and university courses. Maths runs on the engine's own course (Analyse: derivatives). Physics, computer science, linear algebra and economics test the upload path and the "not in the course" guard.

## How to run

Run flow and UI checks on the preview engine, and content and language checks on the real engine. The preview engine answers every question with the same French text.

| Mode | Command | Use it for |
| --- | --- | --- |
| Preview engine | `app/dev.sh` | Screens, flows, rooms, voice, map, prep. No key, no quota. |
| Real engine (FelCloud) | `app/dev.sh --real` | Answer quality, languages, grounding. Spends the team quota (20 requests/min). |
| Local models | `app/dev.sh --ollama` | Content checks outside a FelCloud slot. Needs Ollama and about 8 GB free RAM. |

- Open http://127.0.0.1:5173, sign in, and pick Student or Professor. There are no real accounts: "View as" in the top bar switches role.
- A fresh browser profile is a new learner with no mastery. Use one profile per persona.
- Group cases need two browsers (one in a private window) in the same room. Voice needs headphones, and a microphone only works on localhost or HTTPS.
- Degraded AI states can be forced on the preview engine through the API only, e.g. `curl -s 127.0.0.1:3000/api/ai/run -H 'content-type: application/json' -d '{"task":"explain","inputs":{"question":"x"},"context":{"fake_outcome":"quota_exhausted"},"learner_id":"t1"}'`. The outcome names are listed in `app/HANDOFF.md`.
- Mark each case in its Result column and add a comment on the row with what you saw.

### What each subject has in the engine

| Subject | Level | In the engine | What the cases test |
| --- | --- | --- | --- |
| Maths: derivatives | Bac (3e/4e) and L1 Analyse | Full course: 11 concepts, course text, exercise bank (French), map, prep | Every feature, with real content |
| Maths: limits | Bac and L1 | Draft only: not on the map, course text under review | Thin or "not in the course" answers |
| Maths: linear algebra | University | Nothing | Professor upload, then grounded answers |
| Physics: kinematics, mechanics | Bac and university | Nothing | "Not in the course" guard, then upload |
| Computer science: algorithms, data structures | Bac info and L2 | Nothing | "Not in the course" guard, then upload; voice in a group |
| Economics | University L1 | Nothing | Out-of-scope behaviour on every screen |

The Diagram Generator needs no course: it draws any function from a sentence (no AI model, no quota).

## Personas

All personas are fictional. Each one uses its own browser profile.

| Persona | Level and course | Languages | Device | Role in the app |
| --- | --- | --- | --- | --- |
| Yasmine | 4e Maths (Bac) | Darija, Arabizi, French | Phone | Student |
| Adam | 3e Sciences expérimentales: maths and physics | French | Laptop | Student |
| Mr Gharbi | High-school physics teacher | French | Laptop | Professor |
| Mme Trabelsi | High-school maths teacher | French, Arabic | Laptop | Professor |
| Ines | L1 Mathématiques-Informatique: Analyse 1 | French, English | Laptop | Student |
| Omar | L2 Computer Science: data structures | English | Laptop | Student |
| Salma | L1 Economics | French | Phone | Student |
| Dr Haddad | University: Analyse 1 and linear algebra | French | Laptop | Professor |

## High-school cases

14 cases: Bac maths runs on real course content; physics and computer science check the "not in the course" guard and the teacher's upload.

| ID | Case | Persona · subject | Steps | Expected | Result |
| --- | --- | --- | --- | --- | --- |
| HS-01 | Level test on a first visit | Yasmine · maths | 1) Assessment Prep → New exam "Bac blanc", 14 days ahead. 2) Start the level test. 3) Answer the 3 exercises, one wrong on purpose. | 3 exercises, easy to hard, each graded with feedback. The wrong one gets the common-mistake feedback. Your mastery updates and the map colours the concepts. | Not run |
| HS-02 | Read the progress map | Yasmine · maths | 1) Progress Map after HS-01. 2) Click the Next up star. 3) Study this concept. | 5 steps left to right. Next up has all its prerequisites mastered. The panel lists Builds on and Unlocks. Study opens that concept's study page. | Not run |
| HS-03 | Ask Lumi by voice, in Darija | Yasmine and Adam · maths | 1) Both join room bac-maths, then Join voice. 2) Yasmine holds Hold to ask Lumi: "chnowa el dérivée mta3 x²". 3) Release. | Both see "Listening to Yasmine…", then the transcript and an answer from the course, read aloud on each device. | Not run |
| HS-04 | Typed question in Arabizi | Yasmine · maths | AI companion: "kifech nal9a dérivée mta3 x³+2x ?" | Read as Arabizi. An explanation with course sources, not a bare hint. The derivative given is 3x²+2. | Not run |
| HS-05 | Hints, then an answer | Adam · maths | 1) Study page "Derivative of a product" → Practice. 2) Ask for hints until none are left. 3) For x²(x+1) answer "2x", then "3x²+2x". | Hints arrive one at a time. "2x" is graded incorrect with feedback; "3x²+2x" is correct. Mastery of the product rule goes up. | Not run |
| HS-06 | Check my work on the board | Yasmine and Adam · maths | 1) Board text lines: "f(x) = 3x^2 + 5x", "f'(x) = 6x + 5", "f'(x) = 6x". 2) Select them → AI tab → Check my work. | A verdict per step; the first wrong step is the last line. Most steps are checked without an AI model. | Not run |
| HS-07 | Fact-check a note | Adam · maths | 1) Note "La dérivée de 1/x est 1/x²" → Fact-check. 2) Note "(x²)' = 2x et (1/x)' = 1/x²" → Fact-check. | 1) Contradicted, with the correction −1/x². 2) Partly right, split into two claims with a verdict each. | Not run |
| HS-08 | Read handwriting | Yasmine · maths | Write f(x) = x³ − 2x by hand (or drop a photo), select it → Read handwriting. | The text and LaTeX of the expression. Messy writing shows "The writing was hard to read". Real engine only. | Not run |
| HS-09 | Diagrams to the board | Adam · maths | Diagram Generator: "variation table of (x+1)/(x-2)", then "curve of x^3 - 3x and tangent at 1" → Send to whiteboard. | Asymptote at x = 2, decreasing on both sides. Curve with the tangent y = −2 at x = 1. Items arrive selected on the board. No AI quota used. | Not run |
| HS-10 | Physics question, no material | Adam · physics | AI companion: "Quelle est la formule de la vitesse moyenne ?" | "Not in the course material". No invented formula. | Not run |
| HS-11 | Teacher uploads physics notes | Mr Gharbi, then Adam · physics | 1) Professor → Course Materials → upload a text PDF "Cinématique", Whole course. 2) Adam repeats HS-10. | "Ready for the AI · n sections"; concept tags "to confirm". Adam's answer cites the notes with a Professor source. Real engine. | Not run |
| HS-12 | Bac computer science question | Adam · computer science | AI companion: "Écris un algorithme qui calcule la somme d'un tableau". | "Not in the course material", no code invented. Not answered as off-topic (it is a study question). | Not run |
| HS-13 | Teacher reviews a reported answer | Yasmine, then Mme Trabelsi · maths | 1) Yasmine: Report answer on an explanation. 2) Mme Trabelsi (Professor) → AI review → Reject. 3) Yasmine asks the same question again. | The item shows question, answer and the report. "Rejected — removed from the cache". The new answer is not marked "earlier answer". | Not run |
| HS-14 | Arabic on a phone | Yasmine · maths | Switch to AR, then open the dashboard, the Progress Map and a room. | Right-to-left layout in the Arabic font. The map is a list by step on a phone; on a wide screen it reads right to left. | Not run |

## University cases

13 cases: Analyse 1 runs on the engine's course; linear algebra, data structures and economics depend on what professors upload.

| ID | Case | Persona · subject | Steps | Expected | Result |
| --- | --- | --- | --- | --- | --- |
| UNI-01 | Formal definition in English | Ines · Analyse 1 | AI companion: "What is the derivative of f at a point, formally?" | An answer built on the limit of the rate of change, with course sources and an exercise to try. Progress shows while it works. | Not run |
| UNI-02 | Revision plan before a midterm | Ines · Analyse 1 | 1) Assessment Prep → New exam "Analyse 1 partiel", 5 days ahead. 2) 60 minutes per day → Build my plan. 3) Open day 1, Study. | Sessions per day, prerequisites before what builds on them, weak concepts early. The study page has an explanation, a worked example and practice. | Not run |
| UNI-03 | Fact-check a compound claim | Ines · Analyse 1 | Board note "f dérivable en a donc continue en a, et la réciproque est vraie" → Fact-check. | Split into two claims. The converse is never marked Supported: Contradicted or Not in the course. | Not run |
| UNI-04 | Quotient with a sign error | Ines · Analyse 1 | Board lines: "f(x) = (x+1)/(x-2)", "f'(x) = ((x-2) + (x+1))/(x-2)^2" → Check my work. | The second line is the first wrong step, with a note on the sign (the right numerator is −3). | Not run |
| UNI-05 | Linear algebra notes for one class | Dr Haddad, then Ines · linear algebra | 1) Professor uploads "Matrices, chapitre 2" (Markdown), visibility My class or group. 2) Ines asks "Comment calculer le déterminant d'une matrice 2x2 ?" | Intended: a grounded answer with a Professor source. Known risk: the app sends no class id today, so Ines may not see class-only files (see Expected gaps). Repeat with Whole course. | Not run |
| UNI-06 | Scanned lecture notes | Dr Haddad · linear algebra | Upload a scanned PDF (photographed pages). | Real engine: pages read by OCR (up to 20 per file), then "Ready for the AI". Preview engine: "Scanned or Word file — needs the full engine". | Not run |
| UNI-07 | Group voice session in CS | Omar and two classmates · data structures | 1) Three people Join voice in room ds-l2. 2) Omar asks Lumi: "What is the time complexity of binary search?" 3) He asks: "Who won the match yesterday?" | Everyone hears everyone, with speaking rings. 2) A spoken "not in the course" answer unless notes were uploaded. 3) A fixed line steering back to the session, with no AI answer generated. | Not run |
| UNI-08 | Two asks at once | Omar and Ines · any | 1) Both hold Ask Lumi together. 2) While Lumi answers, Ines asks in the board's AI tab. | 1) The second gets "Lumi is answering, wait a moment". 2) "Someone in this room is already asking the AI — try again in N s" with a countdown. | Not run |
| UNI-09 | Professor dashboard | Dr Haddad · Analyse 1 | View as Professor → Dashboard, then AI review. | Class figures, weak points and roster. Figures that are demo data carry "Example data". AI review lists flagged answers or "Nothing to review". | Not run |
| UNI-10 | Reply language override | Ines · Analyse 1 | Settings → AI replies → English, then ask in French: "C'est quoi la dérivée d'un produit ?" | The answer is in English. | Not run |
| UNI-11 | Economics, out of scope | Salma · economics | 1) AI companion: "Qu'est-ce que l'élasticité-prix ?" 2) Open Assessment Prep and the Progress Map. | 1) "Not in the course material". 2) Prep says AI practice covers Analyse for now; the map shows Analyse only. | Not run |
| UNI-12 | Concept map from mastery | Ines · Analyse 1 | Diagram Generator: "concept map around the product rule" → Send to whiteboard. | The product rule with what it builds on, coloured by Ines's real mastery. A learner with no attempts gets no mastery colours. | Not run |
| UNI-13 | Follow-up questions | Ines · Analyse 1 | 1) Ask about the product rule. 2) Follow up: "Et pour un quotient ?" 3) New chat, ask the follow-up again. | 2) The answer uses the earlier turn. 3) After New chat the follow-up alone is treated as a new question. | Not run |

## Cross-cutting cases

9 cases for any level: the AI must stay honest when it is slow, out of quota or offline, and every screen must work in each language and theme.

| ID | Case | Steps | Expected | Result |
| --- | --- | --- | --- | --- |
| X-01 | Quota used up | Preview engine: force `quota_exhausted` on explain (curl in How to run). Real engine: run during a load test. | "The AI quota is used up for now. Try again in N s" with a countdown. In voice, Lumi says the wait time aloud. | Not run |
| X-02 | Busy model, simplified answer | Force `fast_extractive` on explain. | "Simplified answer — course excerpts while the AI is busy", the excerpts, and "Get the full explanation". | Not run |
| X-03 | A formula looks wrong | Force `wrong_math` on explain. | "A formula in this answer looks wrong. It has been sent to your professor for review." The answer appears in AI review. | Not run |
| X-04 | AI service down, then back | 1) Stop the bridge (kill its process). 2) Ask a question, and ask Lumi in voice. 3) Restart with `app/dev.sh`. | "AI engine offline"; "The AI service isn't reachable"; voice shows a clean error and the room is not stuck. Answers work again within about 15 s of the restart. | Not run |
| X-05 | Same question twice | Ask the same explanation twice in a row. | The second answer is instant and marked "earlier answer". | Not run |
| X-06 | Three interface languages | Switch FR, EN, AR on the dashboard, map, prep, board and voice panel. | Every label translated; Arabic right to left; learner and AI text keeps its own direction. | Not run |
| X-07 | Light, dark and system themes | Settings → Display → Appearance: Dark, Light, System (change the OS setting while open). | Every screen readable in both themes; System follows the OS live; the choice survives a reload. | Not run |
| X-08 | Keyboard and reduced motion | Tab through the map and the voice panel; turn on reduced motion in the OS. | Every star and button reachable with a visible focus ring; Enter selects a star; Space holds Ask Lumi. No pulsing or drifting with reduced motion. | Not run |
| X-09 | Microphone refused | Deny the microphone, then Join voice. | "Microphone permission was denied…" and the board keeps working. | Not run |

## Expected gaps

These limits are known today. Mark the case Pass if only the gap shows, and Fail if anything else goes wrong.

| Gap | Affects | Detail |
| --- | --- | --- |
| One course has content | HS-10 to HS-12, UNI-05 to UNI-07, UNI-11 | Only Analyse (derivatives) has course text, a map, prep and exercises. Other subjects answer only from uploaded notes. |
| Exercises in French only | HS-01, HS-05, UNI-02 | The exercise bank is French; statements are not translated. |
| Limits is a draft | Any question about limits | The concept is not on the map and its course text is under review. |
| Darija answers in French | HS-03, HS-04 | The answer body is often French; Lumi then reads it with a French voice. Log a Fail only if the maths is wrong. |
| Class-only files hidden from students | UNI-05 | Fixed 2026-11-11: the app sends the demo class id (`l1-mi-g1`) with uploads, lists and AI requests. There is still one class and no enrolment, so every signed-in user is in it. |
| Preview engine is canned | All on `app/dev.sh` | Every answer is the same French text. Degraded states are forced through the API, not from the screen. |
| Speech untested with real voices | HS-03, UNI-07 | English was transcribed correctly in tests; French, Arabic and Darija accuracy is unknown. Voices for reading aloud depend on the device; a missing Arabic voice shows "No Arabic voice on this device, reading as text". |
| Handwriting never tried on a real photo | HS-08 | Only typed and synthetic samples so far. |
| Limits stated without a point | X-03 | "La limite de … est égale à …" is listed as unchecked, not flagged as wrong. |
| Demo figures on the professor dashboard | UNI-09 | Class figures are partly example data and are marked "Example data". |
| No real accounts | All | Roles are chosen in the browser. Lumi in voice is open to everyone until tiers exist. |
