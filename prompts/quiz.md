---
task: quiz
version: 2
---
## system
The exercise itself comes from the exercise bank; this prompt only writes its one-line introduction.

## intro_system
Write ONE short sentence (max 12 words) that announces a practice exercise on the given topic, addressing the
student as "tu". Do not invent any task, do not solve or restate the exercise, no praise. $language_rule
Example for the topic "tangente": Voici un exercice sur la tangente pour t'entraîner.

## intro_user
Topic of the exercise: $topic

## line.DARIJA
Write it in Tunisian Darija, Arabic letters (متاع, شنوة, كيفاش, باهي; never ديال, واش). If unsure of a word, use the French term.

## line.ARABIZI
Write it in Tunisian Darija, Latin letters (mta3, chnowa, kifech, behi; never dyal, wach). If unsure of a word, use the French term.

## line.FRENCH
Write it in French.

## line.ARABIC
Write it in Modern Standard Arabic.

## line.ENGLISH
Write it in English.

## intro_template.DARIJA
هاو تمرين باش تتدرب:

## intro_template.ARABIZI
Hak exercice bech tetdarreb:

## intro_template.FRENCH
Voici un exercice pour t'entraîner :

## intro_template.ARABIC
إليك تمرينا للتدرب:

## intro_template.ENGLISH
Here is an exercise to practise:

## diagnostic.DARIJA
ثلاثة تمارين قصار، من الأسهل للأصعب. جاوب على الأول:

## diagnostic.ARABIZI
Thletha exercices sghar, mel as-hel lel as3eb. Jaweb 3al lowel:

## diagnostic.FRENCH
Trois exercices courts, du plus simple au plus difficile. Réponds au premier :

## diagnostic.ARABIC
ثلاثة تمارين قصيرة، من الأسهل إلى الأصعب. أجب عن الأول:

## diagnostic.ENGLISH
Three short exercises, from easiest to hardest. Answer the first one:

## empty.FRENCH
Tu as fait tous les exercices disponibles sur ce thème.

## empty.ENGLISH
You have done all the exercises available on this topic.

## empty.ARABIC
لقد أنجزت كل التمارين المتوفرة في هذا الموضوع.

## empty.DARIJA
كمّلت التمارين الكل اللي عندي على الموضوع هذا.

## empty.ARABIZI
Kammelt les exercices lkol eli 3andi 3al sujet hedha.
