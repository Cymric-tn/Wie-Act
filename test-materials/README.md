# Test materials: high-school and university courses

Openly licensed course material for the test scenarios in "Lumi test scenarios: high school and university"
(https://claude.ai/artifact/DuihnT8euweKdLnH7LYJeH). Upload a file in **Course Materials** (Professor view) to test
grounded answers outside the engine's own course (Analyse: derivatives). Every file is under the app's 15 MB limit.
Retrieved 2026-11-10.

- `.md` and `.txt` files are indexed by any engine, the preview engine included.
- `.pdf` files need the real engine with PyMuPDF installed (`app/dev.sh --real` or `--ollama`).
- `exo7-determinants-scan.pdf` has no text layer: only the engine's OCR can read it (UNI-06).
- Markdown files start with their source, author and licence. Text extracts start with the same lines.

## High school (`lycee/`)

| File | Subject | Language | Licence | Test cases |
| --- | --- | --- | --- | --- |
| `physique/openstax-physics-2-2-speed-and-velocity.md` | Kinematics: average speed and velocity | English | CC BY 4.0, [OpenStax Physics](https://openstax.org/books/physics/pages/2-2-speed-and-velocity) | HS-10, HS-11 (cross-language: French question, English notes) |
| `physique/wikipedia-fr-vitesse.md` | Speed, average speed | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/Vitesse) | HS-10, HS-11 |
| `physique/wikipedia-fr-cinematique.md` | Kinematics (long article) | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/Cin%C3%A9matique) | HS-11 |
| `informatique/programme-officiel-informatique-3e-4e-tunisie-2006.pdf` and `.txt` | Official Tunisian informatics programme, 3e and 4e | French | Public curriculum document, Ministère de l'Éducation ([HAL copy](https://edutice.hal.science/edutice-00560705/file/a1002f_3_4.pdf)) | HS-12 |
| `informatique/wikipedia-fr-tableau-structure-de-donnees.md` | Arrays | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/Tableau_(structure_de_donn%C3%A9es)) | HS-12 |
| `informatique/wikipedia-fr-algorithme-de-tri.md` | Sorting algorithms | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/Algorithme_de_tri) | HS-12 |

## University (`universite/`)

| File | Subject | Language | Licence | Test cases |
| --- | --- | --- | --- | --- |
| `analyse-1/exo7-derivee-d-une-fonction.pdf` and `.txt` | Derivatives (L1) | French | CC BY-NC-SA 3.0 FR, [Exo7](https://exo7.emath.fr/cours/ch_derivee.pdf) | UNI-01 (professor source next to the engine's course) |
| `analyse-1/exo7-limites-et-fonctions-continues.pdf` and `.txt` | Limits and continuity (L1) | French | CC BY-NC-SA 3.0 FR, [Exo7](https://exo7.emath.fr/cours/ch_fonctions.pdf) | UNI-03 (continuity claim), limits questions |
| `algebre-lineaire/exo7-matrices.pdf` and `.txt` | Matrices | French | CC BY-NC-SA 3.0 FR, [Exo7](https://exo7.emath.fr/cours/ch_matrices.pdf) | UNI-05 |
| `algebre-lineaire/exo7-determinants.pdf` and `.txt` | Determinants (2×2: ad − bc) | French | CC BY-NC-SA 3.0 FR, [Exo7](https://exo7.emath.fr/cours/ch_determinants.pdf) | UNI-05 |
| `algebre-lineaire/exo7-determinants-scan.pdf` | First 4 pages of the above as a scanned, image-only PDF | French | Same as the source (an image of it) | UNI-06 (OCR) |
| `structures-de-donnees/wikipedia-en-binary-search.md` | Binary search, complexity | English | CC BY-SA 4.0, [Wikipedia](https://en.wikipedia.org/wiki/Binary_search) | UNI-07 |
| `structures-de-donnees/wikipedia-fr-recherche-dichotomique.md` | Binary search | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/Recherche_dichotomique) | UNI-07 |
| `economie/openstax-microeconomics-5-1-price-elasticity.md` | Price elasticity of demand and supply | English | CC BY-NC-SA 4.0, [OpenStax Principles of Microeconomics 3e](https://openstax.org/books/principles-microeconomics-3e/pages/5-1-price-elasticity-of-demand-and-price-elasticity-of-supply) | UNI-11 |
| `economie/wikipedia-fr-elasticite-economie.md` | Elasticity (economics) | French | CC BY-SA 4.0, [Wikipédia](https://fr.wikipedia.org/wiki/%C3%89lasticit%C3%A9_(%C3%A9conomie)) | UNI-11 |

## Licences

Exo7 and the OpenStax microeconomics text are non-commercial (NC): fine for testing and the challenge demo. Do not
ship them in a paid product. Share-alike files (SA) keep their licence if you modify and share them. The `.txt`
extracts and the scanned PDF are adaptations made for testing, under the same licence as their source.

Not included: a real handwriting photo for HS-08 (take one yourself), and Word (.docx) files.
