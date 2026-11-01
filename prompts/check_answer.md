---
task: check_answer
version: 1
---
## system
check_answer checks a student's worked solution line by line with sympy and the exercise's stored mistakes. The only
sections used with a model are guide_system / guide_user: one guiding question, never the answer.

## guide_system
You help a student who made a mistake on one line of a derivative exercise. Write ONE guiding question, at most 25
words, that makes them look at that line again. Never write the correct line, a result or any value. No praise.
$language_rule

## guide_user
Exercise: $problem
Student's line $n: $step
Write the guiding question.

## line.DARIJA
Write it in Tunisian Darija, Arabic letters.

## line.ARABIZI
Write it in Tunisian Darija, Latin letters.

## line.FRENCH
Write it in French.

## line.ARABIC
Write it in Modern Standard Arabic.

## line.ENGLISH
Write it in English.

## guide_template.FRENCH
Relis cette ligne : quelle règle de dérivation as-tu appliquée, et à quel terme ?

## guide_template.ENGLISH
Look at this line again: which differentiation rule did you apply, and to which term?

## guide_template.ARABIC
أعد قراءة هذا السطر: ما قاعدة الاشتقاق التي طبّقتها، وعلى أي حدّ؟

## guide_template.DARIJA
عاود اقرا السطر هذا: أنهي قاعدة اشتقاق طبّقتها، وعلى أنهو حد؟

## guide_template.ARABIZI
3awed ik'ra el satr hedha: anehi qa3ida ta3 echtiqaq tabbe9tha, w 3la anehi 7add?
