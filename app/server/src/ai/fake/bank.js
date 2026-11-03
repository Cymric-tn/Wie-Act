// Course content for the built-in fake engine: one topic (derivatives, the
// real engine's first bank), a course graph, a glossary for Darija / Arabizi /
// Arabic learner words, exercises with graded hints, and reply templates.
// It only exists so every screen can be built and tested before the real
// engine is plugged in; the real engine brings its own data/ folder.

const COURSE = {
  id: "MATH101",
  title: { fr: "Analyse I — Dérivées", en: "Calculus I — Derivatives", ar: "التحليل 1 — الاشتقاق" },
  units: [
    { id: "u1", order: 1, title: { fr: "Le nombre dérivé", en: "The derivative", ar: "العدد المشتق" }, concepts: ["definition", "tangente"] },
    { id: "u2", order: 2, title: { fr: "Règles de calcul", en: "Differentiation rules", ar: "قواعد الاشتقاق" }, concepts: ["puissance", "somme", "produit", "quotient"] },
    { id: "u3", order: 3, title: { fr: "Composition et variations", en: "Chain rule and variations", ar: "التركيب وتغيّرات الدالة" }, concepts: ["composee", "variations"] }
  ],
  concepts: {
    definition: { requires: [], title: { fr: "Définition de la dérivée", en: "Definition of the derivative", ar: "تعريف المشتقة" } },
    tangente: { requires: ["definition"], title: { fr: "Tangente", en: "Tangent line", ar: "المماس" } },
    puissance: { requires: ["definition"], title: { fr: "Dérivée d’une puissance", en: "Power rule", ar: "مشتقة القوة" } },
    somme: { requires: ["puissance"], title: { fr: "Somme et constante", en: "Sum and constant rule", ar: "المجموع والثابت" } },
    produit: { requires: ["somme"], title: { fr: "Dérivée d’un produit", en: "Product rule", ar: "مشتقة الجداء" } },
    quotient: { requires: ["produit"], title: { fr: "Dérivée d’un quotient", en: "Quotient rule", ar: "مشتقة خارج القسمة" } },
    composee: { requires: ["produit"], title: { fr: "Fonction composée", en: "Chain rule", ar: "مشتقة دالة مركّبة" } },
    variations: { requires: ["somme"], title: { fr: "Sens de variation", en: "Increasing and decreasing", ar: "اتجاه التغيّر" } }
  }
};

// Course chunks, one per concept and language. Ids follow <file stem>#<slug>.
const CHUNKS = [
  { id: "derivees.fr#definition", concept: "definition", language: "FRENCH", title: "Définition", text: "La dérivée de f en a est la limite du taux d’accroissement (f(a+h) − f(a))/h quand h tend vers 0. Elle mesure la vitesse de variation de f. Le nombre dérivé f′(a) est la pente de la tangente à la courbe au point d’abscisse a." },
  { id: "derivees.fr#puissance", concept: "puissance", language: "FRENCH", title: "Puissances", text: "Pour f(x) = xⁿ, on a f′(x) = n·xⁿ⁻¹. Par exemple (x³)′ = 3x². La dérivée d’une constante est 0 et la dérivée de x est 1. Pour la racine : (√x)′ = 1/(2√x) pour x > 0." },
  { id: "derivees.fr#somme", concept: "somme", language: "FRENCH", title: "Somme et constante", text: "La dérivée d’une somme est la somme des dérivées : (u + v)′ = u′ + v′. Une constante multiplicative se garde : (k·u)′ = k·u′. Ainsi (3x² + 5x)′ = 6x + 5." },
  { id: "derivees.fr#produit", concept: "produit", language: "FRENCH", title: "Produit", text: "Pour un produit de deux fonctions u et v : (u·v)′ = u′·v + u·v′. Ce n’est pas u′·v′. Par exemple pour f(x) = x(x + 1), u = x et v = x + 1, donc f′(x) = 1·(x + 1) + x·1 = 2x + 1." },
  { id: "derivees.fr#quotient", concept: "quotient", language: "FRENCH", title: "Quotient", text: "Pour un quotient u/v avec v ≠ 0 : (u/v)′ = (u′·v − u·v′)/v². En particulier (1/v)′ = −v′/v². Le signe moins et le carré au dénominateur sont les erreurs les plus fréquentes." },
  { id: "derivees.fr#composee", concept: "composee", language: "FRENCH", title: "Composée", text: "Pour une fonction composée f(x) = g(u(x)), on a f′(x) = u′(x)·g′(u(x)). Par exemple ((2x + 1)³)′ = 2·3(2x + 1)² = 6(2x + 1)². Il ne faut pas oublier de multiplier par u′." },
  { id: "derivees.fr#tangente", concept: "tangente", language: "FRENCH", title: "Tangente", text: "L’équation de la tangente à la courbe de f au point d’abscisse a est y = f′(a)(x − a) + f(a). Sa pente est le nombre dérivé f′(a)." },
  { id: "derivees.fr#variations", concept: "variations", language: "FRENCH", title: "Sens de variation", text: "Si f′(x) > 0 sur un intervalle, f est croissante sur cet intervalle ; si f′(x) < 0, f est décroissante. Un extremum local peut se trouver là où f′ s’annule en changeant de signe." },

  { id: "derivatives.en#definition", concept: "definition", language: "ENGLISH", title: "Definition", text: "The derivative of f at a is the limit of the difference quotient (f(a+h) − f(a))/h as h tends to 0. It measures how fast f changes. f′(a) is the slope of the tangent line at x = a." },
  { id: "derivatives.en#power", concept: "puissance", language: "ENGLISH", title: "Power rule", text: "For f(x) = xⁿ, f′(x) = n·xⁿ⁻¹. For example (x³)′ = 3x². The derivative of a constant is 0 and the derivative of x is 1. For the square root, (√x)′ = 1/(2√x) for x > 0." },
  { id: "derivatives.en#product", concept: "produit", language: "ENGLISH", title: "Product rule", text: "For a product of two functions u and v: (u·v)′ = u′·v + u·v′. It is not u′·v′. For f(x) = x(x + 1), u = x and v = x + 1, so f′(x) = 1·(x + 1) + x·1 = 2x + 1." },
  { id: "derivatives.en#chain", concept: "composee", language: "ENGLISH", title: "Chain rule", text: "For a composite function f(x) = g(u(x)), f′(x) = u′(x)·g′(u(x)). For example ((2x + 1)³)′ = 2·3(2x + 1)² = 6(2x + 1)². Do not forget to multiply by u′." },

  { id: "derivees.ar#produit", concept: "produit", language: "ARABIC", title: "مشتقة الجداء", text: "لجداء دالتين u و v لدينا: (u·v)′ = u′·v + u·v′، وليست u′·v′. مثلًا من أجل f(x) = x(x + 1) نجد f′(x) = 2x + 1." },
  { id: "derivees.ar#puissance", concept: "puissance", language: "ARABIC", title: "مشتقة القوة", text: "من أجل f(x) = xⁿ لدينا f′(x) = n·xⁿ⁻¹. مثلًا (x³)′ = 3x². مشتقة الثابت تساوي 0 ومشتقة x تساوي 1." }
];

// Learner words (Darija, Arabizi, MSA, English) → the French course terms.
const GLOSSARY = [
  { fr: "dérivée", variants: ["derivee", "derive", "dérivé", "derivative", "derivatives", "derivation", "mochtaqa", "mochta9a", "mochtakka", "ichti9a9", "ichtiqaq", "مشتقة", "المشتقة", "اشتقاق", "الاشتقاق", "مشتق"] },
  { fr: "produit", variants: ["product", "produits", "darb", "dharb", "jida2", "jidaa", "جداء", "الجداء", "ضرب", "حاصل", "uv", "u'v", "u'v'", "uv'"] },
  { fr: "quotient", variants: ["u/v", "(u/v)'"] },
  { fr: "quotient", variants: ["division", "9isma", "qisma", "kharij", "قسمة", "خارج"] },
  { fr: "composée", variants: ["composee", "compose", "chain", "composite", "tarkib", "مركبة", "تركيب"] },
  { fr: "puissance", variants: ["power", "exponent", "puissances", "9owa", "qowa", "قوة", "أس"] },
  { fr: "tangente", variants: ["tangent", "momas", "مماس", "المماس"] },
  { fr: "variation", variants: ["croissante", "décroissante", "decroissante", "increasing", "decreasing", "taghayor", "تغير", "متزايدة", "متناقصة"] },
  { fr: "somme", variants: ["sum", "addition", "majmou3", "مجموع"] },
  { fr: "limite", variants: ["limit", "nihaya", "نهاية"] },
  { fr: "racine", variants: ["sqrt", "root", "square", "jadhr", "جذر"] }
];

/** Exercise bank. Solutions, hints and mistakes never leave the server. */
const EXERCISES = [
  { id: "d01", concept: "puissance", level: 1, expression: "x^4", statement: { fr: "Calculer la dérivée de f(x) = x⁴.", en: "Find the derivative of f(x) = x⁴.", ar: "احسب مشتقة f(x) = x⁴." }, solution: "4x^3",
    hints: { fr: ["Quelle règle s’applique à xⁿ ?", "Pour xⁿ, l’exposant descend devant. Que devient l’exposant ?", "Multiplie par 4, puis diminue l’exposant de 1."], en: ["Which rule applies to xⁿ?", "For xⁿ the exponent comes down in front. What happens to the exponent?", "Multiply by 4, then lower the exponent by 1."], ar: ["ما القاعدة التي تنطبق على xⁿ؟", "في xⁿ ينزل الأس إلى الأمام. ماذا يحدث للأس؟", "اضرب في 4 ثم أنقص الأس بواحد."] },
    mistakes: [{ expr: "x^3", feedback: { fr: "Tu as baissé l’exposant mais oublié de le multiplier devant.", en: "You lowered the exponent but forgot to bring it down in front.", ar: "أنقصت الأس لكنك نسيت ضربه في المقدمة." } }, { expr: "4x^4", feedback: { fr: "Le coefficient est bon, mais l’exposant doit diminuer de 1.", en: "The coefficient is right, but the exponent must go down by 1.", ar: "المعامل صحيح لكن يجب إنقاص الأس بواحد." } }] },
  { id: "d02", concept: "somme", level: 1, expression: "3x^2+5x", statement: { fr: "Calculer la dérivée de f(x) = 3x² + 5x.", en: "Find the derivative of f(x) = 3x² + 5x.", ar: "احسب مشتقة f(x) = 3x² + 5x." }, solution: "6x+5",
    hints: { fr: ["Dérive chaque terme séparément.", "Que vaut la dérivée de 3x² ? Et celle de 5x ?", "(3x²)′ = 6x ; il reste à dériver 5x."], en: ["Differentiate each term separately.", "What is the derivative of 3x²? And of 5x?", "(3x²)′ = 6x; now differentiate 5x."], ar: ["اشتق كل حدّ على حدة.", "ما مشتقة 3x²؟ وما مشتقة 5x؟", "(3x²)′ = 6x؛ بقي اشتقاق 5x."] },
    mistakes: [{ expr: "6x", feedback: { fr: "Tu as oublié de dériver le terme 5x.", en: "You forgot to differentiate the 5x term.", ar: "نسيت اشتقاق الحدّ 5x." } }, { expr: "6x+5x", feedback: { fr: "La dérivée de 5x est une constante.", en: "The derivative of 5x is a constant.", ar: "مشتقة 5x عدد ثابت." } }] },
  { id: "d03", concept: "puissance", level: 2, expression: "sqrt(x)", statement: { fr: "Calculer la dérivée de f(x) = √x pour x > 0.", en: "Find the derivative of f(x) = √x for x > 0.", ar: "احسب مشتقة f(x) = √x من أجل x > 0." }, solution: "1/(2sqrt(x))",
    hints: { fr: ["Écris √x comme une puissance de x.", "√x = x^(1/2). Applique la règle des puissances.", "Le résultat contient √x au dénominateur."], en: ["Write √x as a power of x.", "√x = x^(1/2). Apply the power rule.", "The answer has √x in the denominator."], ar: ["اكتب √x على شكل قوة لـ x.", "√x = x^(1/2). طبّق قاعدة القوة.", "الناتج يحتوي على √x في المقام."] },
    mistakes: [{ expr: "1/sqrt(x)", feedback: { fr: "Il manque le facteur 1/2.", en: "The factor 1/2 is missing.", ar: "ينقص المعامل 1/2." } }] },
  { id: "d04", concept: "produit", level: 1, expression: "x(x+1)", statement: { fr: "Calculer la dérivée de f(x) = x(x + 1).", en: "Find the derivative of f(x) = x(x + 1).", ar: "احسب مشتقة f(x) = x(x + 1)." }, solution: "2x+1",
    hints: { fr: ["Repère les deux facteurs u et v.", "u = x et v = x + 1 : que valent u′ et v′ ?", "Applique (u·v)′ = u′·v + u·v′."], en: ["Identify the two factors u and v.", "u = x and v = x + 1: what are u′ and v′?", "Apply (u·v)′ = u′·v + u·v′."], ar: ["حدّد العاملين u و v.", "u = x و v = x + 1: ما قيمة u′ و v′؟", "طبّق (u·v)′ = u′·v + u·v′."] },
    mistakes: [{ expr: "1", feedback: { fr: "Tu as multiplié les dérivées : (u·v)′ n’est pas u′·v′.", en: "You multiplied the derivatives: (u·v)′ is not u′·v′.", ar: "ضربت المشتقتين: (u·v)′ ليست u′·v′." } }] },
  { id: "d05", concept: "produit", level: 2, expression: "x(x+3)", statement: { fr: "Calculer la dérivée de f(x) = x(x + 3).", en: "Find the derivative of f(x) = x(x + 3).", ar: "احسب مشتقة f(x) = x(x + 3)." }, solution: "2x+3",
    hints: { fr: ["C’est un produit : quels sont u et v ?", "u′ = 1 et v′ = 1. Écris u′·v + u·v′.", "Développe 1·(x + 3) + x·1."], en: ["It is a product: what are u and v?", "u′ = 1 and v′ = 1. Write u′·v + u·v′.", "Expand 1·(x + 3) + x·1."], ar: ["إنه جداء: ما هما u و v؟", "u′ = 1 و v′ = 1. اكتب u′·v + u·v′.", "انشر 1·(x + 3) + x·1."] },
    mistakes: [{ expr: "1", feedback: { fr: "(u·v)′ n’est pas u′·v′.", en: "(u·v)′ is not u′·v′.", ar: "(u·v)′ ليست u′·v′." } }, { expr: "x+3", feedback: { fr: "Tu n’as gardé qu’un des deux termes de la formule.", en: "You kept only one of the two terms of the rule.", ar: "احتفظت بحدّ واحد فقط من القاعدة." } }] },
  { id: "d06", concept: "produit", level: 3, expression: "x^2(3x-1)", statement: { fr: "Calculer la dérivée de f(x) = x²(3x − 1).", en: "Find the derivative of f(x) = x²(3x − 1).", ar: "احسب مشتقة f(x) = x²(3x − 1)." }, solution: "9x^2-2x",
    hints: { fr: ["Choisis u = x² et v = 3x − 1.", "u′ = 2x, v′ = 3. Assemble u′·v + u·v′.", "Développe 2x(3x − 1) + 3x² puis regroupe."], en: ["Take u = x² and v = 3x − 1.", "u′ = 2x, v′ = 3. Assemble u′·v + u·v′.", "Expand 2x(3x − 1) + 3x² and collect terms."], ar: ["خذ u = x² و v = 3x − 1.", "u′ = 2x و v′ = 3. ركّب u′·v + u·v′.", "انشر 2x(3x − 1) + 3x² ثم اجمع الحدود."] },
    mistakes: [{ expr: "6x", feedback: { fr: "Tu as multiplié u′ et v′ : utilise u′·v + u·v′.", en: "You multiplied u′ and v′: use u′·v + u·v′.", ar: "ضربت u′ في v′: استعمل u′·v + u·v′." } }] },
  { id: "d07", concept: "quotient", level: 2, expression: "1/x", statement: { fr: "Calculer la dérivée de f(x) = 1/x pour x ≠ 0.", en: "Find the derivative of f(x) = 1/x for x ≠ 0.", ar: "احسب مشتقة f(x) = 1/x من أجل x ≠ 0." }, solution: "-1/x^2",
    hints: { fr: ["Utilise (1/v)′ = −v′/v².", "Ici v = x : que vaut v′ ?", "N’oublie pas le signe moins."], en: ["Use (1/v)′ = −v′/v².", "Here v = x: what is v′?", "Don’t forget the minus sign."], ar: ["استعمل (1/v)′ = −v′/v².", "هنا v = x: ما قيمة v′؟", "لا تنسَ إشارة الناقص."] },
    mistakes: [{ expr: "1/x^2", feedback: { fr: "Le signe est faux : (1/x)′ est négatif.", en: "The sign is wrong: (1/x)′ is negative.", ar: "الإشارة خاطئة: (1/x)′ سالبة." } }] },
  { id: "d08", concept: "quotient", level: 3, expression: "(x+1)/(x-1)", statement: { fr: "Calculer la dérivée de f(x) = (x + 1)/(x − 1).", en: "Find the derivative of f(x) = (x + 1)/(x − 1).", ar: "احسب مشتقة f(x) = (x + 1)/(x − 1)." }, solution: "-2/(x-1)^2",
    hints: { fr: ["Utilise (u/v)′ = (u′v − uv′)/v².", "u′ = 1 et v′ = 1 : calcule le numérateur.", "(x − 1) − (x + 1) = ?"], en: ["Use (u/v)′ = (u′v − uv′)/v².", "u′ = 1 and v′ = 1: compute the numerator.", "(x − 1) − (x + 1) = ?"], ar: ["استعمل (u/v)′ = (u′v − uv′)/v².", "u′ = 1 و v′ = 1: احسب البسط.", "(x − 1) − (x + 1) = ؟"] },
    mistakes: [{ expr: "2/(x-1)^2", feedback: { fr: "L’ordre du numérateur est u′v − uv′ : le signe change.", en: "The numerator is u′v − uv′: the sign flips.", ar: "البسط هو u′v − uv′: الإشارة تتغيّر." } }] },
  { id: "d09", concept: "composee", level: 2, expression: "(2x+1)^3", statement: { fr: "Calculer la dérivée de f(x) = (2x + 1)³.", en: "Find the derivative of f(x) = (2x + 1)³.", ar: "احسب مشتقة f(x) = (2x + 1)³." }, solution: "6(2x+1)^2",
    hints: { fr: ["C’est une composée : quelle est la fonction intérieure u ?", "u = 2x + 1 et g(u) = u³. Que vaut u′ ?", "f′ = u′·3u²."], en: ["It is a composite: what is the inner function u?", "u = 2x + 1 and g(u) = u³. What is u′?", "f′ = u′·3u²."], ar: ["إنها دالة مركّبة: ما الدالة الداخلية u؟", "u = 2x + 1 و g(u) = u³. ما قيمة u′؟", "f′ = u′·3u²."] },
    mistakes: [{ expr: "3(2x+1)^2", feedback: { fr: "Tu as oublié de multiplier par u′ = 2.", en: "You forgot to multiply by u′ = 2.", ar: "نسيت الضرب في u′ = 2." } }] },
  { id: "d10", concept: "definition", level: 1, expression: "x^2", statement: { fr: "Pour f(x) = x², calculer le nombre dérivé f′(3).", en: "For f(x) = x², compute f′(3).", ar: "من أجل f(x) = x²، احسب f′(3)." }, solution: "6",
    hints: { fr: ["Commence par calculer f′(x).", "f′(x) = 2x. Remplace x par 3.", "2 × 3 = ?"], en: ["Start by finding f′(x).", "f′(x) = 2x. Substitute x = 3.", "2 × 3 = ?"], ar: ["ابدأ بحساب f′(x).", "f′(x) = 2x. عوّض x بـ 3.", "2 × 3 = ؟"] },
    mistakes: [{ expr: "9", feedback: { fr: "9 est f(3), pas f′(3).", en: "9 is f(3), not f′(3).", ar: "9 هي f(3) وليست f′(3)." } }] },
  { id: "d11", concept: "tangente", level: 2, expression: "x^2", statement: { fr: "Quelle est la pente de la tangente à y = x² au point d’abscisse 1 ?", en: "What is the slope of the tangent to y = x² at x = 1?", ar: "ما ميل المماس للمنحنى y = x² عند x = 1؟" }, solution: "2",
    hints: { fr: ["La pente de la tangente est un nombre dérivé.", "Calcule f′(x) pour f(x) = x².", "Évalue f′(1)."], en: ["The tangent’s slope is a derivative value.", "Find f′(x) for f(x) = x².", "Evaluate f′(1)."], ar: ["ميل المماس هو عدد مشتق.", "احسب f′(x) من أجل f(x) = x².", "احسب f′(1)."] },
    mistakes: [{ expr: "1", feedback: { fr: "1 est f(1) : la pente est f′(1).", en: "1 is f(1): the slope is f′(1).", ar: "1 هي f(1): الميل هو f′(1)." } }] },
  { id: "d12", concept: "variations", level: 2, expression: "x^2-4x", statement: { fr: "Pour f(x) = x² − 4x, pour quelle valeur de x la dérivée s’annule-t-elle ?", en: "For f(x) = x² − 4x, at which x is the derivative zero?", ar: "من أجل f(x) = x² − 4x، عند أي قيمة لـ x تنعدم المشتقة؟" }, solution: "2",
    hints: { fr: ["Calcule f′(x).", "f′(x) = 2x − 4. Résous f′(x) = 0.", "2x = 4, donc x = ?"], en: ["Find f′(x).", "f′(x) = 2x − 4. Solve f′(x) = 0.", "2x = 4, so x = ?"], ar: ["احسب f′(x).", "f′(x) = 2x − 4. حلّ f′(x) = 0.", "2x = 4، إذن x = ؟"] },
    mistakes: [{ expr: "4", feedback: { fr: "4 annule f, pas f′.", en: "4 is a root of f, not of f′.", ar: "4 تعدم f وليس f′." } }] }
];

const TEMPLATES = {
  opener: {
    FRENCH: { LOW: "Voici l’essentiel du cours sur ce point.", MEDIUM: "Bonne question, voyons ça ensemble.", HIGH: "Pas de souci, on reprend ce point calmement." },
    ENGLISH: { LOW: "Here is the key idea from the course.", MEDIUM: "Good question — let’s look at it together.", HIGH: "No worries, let’s go through it calmly." },
    ARABIC: { LOW: "إليك الفكرة الأساسية من الدرس.", MEDIUM: "سؤال جيّد، لنرَ ذلك معًا.", HIGH: "لا بأس، لنعد إلى هذه النقطة بهدوء." },
    DARIJA: { LOW: "هاذي الفكرة الأساسية متاع الدرس.", MEDIUM: "سؤال باهي، نشوفوها مع بعضنا.", HIGH: "ما فماش مشكل، نعاودوها بالشوية." },
    ARABIZI: { LOW: "Hethi el fekra el asesiya mta3 el cours.", MEDIUM: "So2al behi, nchoufouha m3a ba3dhna.", HIGH: "Ma fama chay, n3awdouha bechwaya." }
  },
  yourTurn: { FRENCH: "À toi :", ENGLISH: "Your turn:", ARABIC: "دورك:", DARIJA: "توّا إنت:", ARABIZI: "Twa enti:" },
  framing: {
    FRENCH: "Le service est chargé : voici les passages du cours qui répondent à ta question.",
    ENGLISH: "The service is busy, so here are the course passages that answer your question.",
    ARABIC: "الخدمة مزدحمة، إليك مقاطع الدرس التي تجيب عن سؤالك.",
    DARIJA: "السيرفيس مكبوس، هاو مقاطع الدرس اللي تجاوب على سؤالك.",
    ARABIZI: "El service mchargi, hethom les passages mta3 el cours."
  },
  notFound: {
    FRENCH: "Je n’ai pas trouvé ce point dans les supports du cours. Pose la question à ton professeur ou reformule avec les termes du cours.",
    ENGLISH: "I couldn’t find this in the course material. Ask your professor, or rephrase it with the course’s terms.",
    ARABIC: "لم أجد هذه النقطة في مراجع الدرس. اسأل أستاذك أو أعد الصياغة بمصطلحات الدرس.",
    DARIJA: "ما لقيتش النقطة هاذي في الدرس. اسأل الأستاذ متاعك ولا عاود الصياغة.",
    ARABIZI: "Ma l9itech hethi fel cours. Es2el el prof wala 3awed ektebha."
  },
  quizIntro: {
    FRENCH: "Voici un exercice pour t’entraîner :", ENGLISH: "Here is an exercise to practise:", ARABIC: "إليك تمرينًا للتدرّب:",
    DARIJA: "هاو تمرين باش تتدرّب:", ARABIZI: "Hak exercice bech tetdarreb:"
  },
  diagnosticIntro: {
    FRENCH: "Petit test de niveau : trois exercices, du plus simple au plus difficile.", ENGLISH: "Quick level check: three exercises, from easiest to hardest.",
    ARABIC: "اختبار مستوى قصير: ثلاثة تمارين من الأسهل إلى الأصعب.", DARIJA: "تست صغير: ثلاثة تمارين من الساهل للصعيب.", ARABIZI: "Test sghir: 3 exercices mel sehel lel s3ib."
  },
  chitchat: {
    FRENCH: "Salut ! Pose-moi une question sur le cours, ou demande un exercice.", ENGLISH: "Hi! Ask me about the course, or ask for an exercise.",
    ARABIC: "مرحبًا! اسألني عن الدرس أو اطلب تمرينًا.", DARIJA: "عسلامة! اسألني على الدرس ولا اطلب تمرين.", ARABIZI: "Aslema! Es2elni 3al cours wala o4lob exercice."
  },
  offTopic: {
    FRENCH: "Je reste sur le cours : pose-moi une question de maths et je t’aide.", ENGLISH: "I stick to the course — ask me a maths question and I’ll help.",
    ARABIC: "أبقى في إطار الدرس: اسألني سؤالًا في الرياضيات وسأساعدك.", DARIJA: "نقعد في الدرس: اسألني في الماث ونعاونك.", ARABIZI: "N93ed fel cours: es2elni fel math w n3awnek."
  },
  feedback: {
    CORRECT: { FRENCH: "Bravo, c’est juste !", ENGLISH: "Well done, that’s right!", ARABIC: "أحسنت، الإجابة صحيحة!" },
    INCORRECT: { FRENCH: "Pas encore : relis ta dernière étape.", ENGLISH: "Not yet — check your last step.", ARABIC: "ليس بعد: راجع خطوتك الأخيرة." },
    UNKNOWN: { FRENCH: "Je n’ai pas pu vérifier cette réponse automatiquement.", ENGLISH: "I couldn’t check this answer automatically.", ARABIC: "لم أتمكّن من التحقّق من هذه الإجابة تلقائيًا." }
  }
};

module.exports = { COURSE, CHUNKS, GLOSSARY, EXERCISES, TEMPLATES };
