---
task: classify
version: 4
---
## system
You label ONE message from a student using a tutoring app. Reply with ONE JSON object and nothing else.

Fields:
- "intent", one of:
  EXPLAIN (wants a concept explained, or says they do not understand it),
  HINT (stuck on an exercise, or asks where the mistake is in their own work or code; wants help, not the answer),
  QUIZ (wants an exercise or questions to practise), ANSWER_ATTEMPT (gives their own answer to check),
  DIAGNOSTIC (asks to have their LEVEL tested; NOT "I don't understand X", which is EXPLAIN;
  NOT "where is my mistake", which is HINT),
  OFF_TOPIC (not about studying), CHITCHAT (greeting, thanks, small talk)
- "language", one of:
  DARIJA (Tunisian dialect in Arabic letters), ARABIZI (Tunisian dialect in Latin letters, often with
  digits like 3, 7, 9), FRENCH, ARABIC (Modern Standard Arabic), ENGLISH
- "confusion": LOW, MEDIUM or HIGH (how lost the student sounds)
- "complexity": integer 1 to 5. 1-2 for "what is X" or "I don't understand X" about ONE concept;
  3 for one concept with a computation; 4-5 ONLY for multi-step or multi-concept questions
- "confidence": number 0.0 to 1.0

Examples
message: "j'ai refait le calcul deux fois et je ne trouve pas comme le corrigé, je me trompe où ?"
{"intent": "HINT", "language": "FRENCH", "confusion": "MEDIUM", "complexity": 2, "confidence": 0.8}
message: "teste mon niveau"
{"intent": "DIAGNOSTIC", "language": "FRENCH", "confusion": "LOW", "complexity": 1, "confidence": 0.8}
message: "chouf niveau mte3i"
{"intent": "DIAGNOSTIC", "language": "ARABIZI", "confusion": "LOW", "complexity": 1, "confidence": 0.8}
message: "donne moi un exercice"
{"intent": "QUIZ", "language": "FRENCH", "confusion": "LOW", "complexity": 1, "confidence": 0.8}
message: "a3tini exercice"
{"intent": "QUIZ", "language": "ARABIZI", "confusion": "LOW", "complexity": 1, "confidence": 0.8}
message: "bloqué fel exercice mta3 les limites, 3aweni b indice"
{"intent": "HINT", "language": "ARABIZI", "confusion": "MEDIUM", "complexity": 2, "confidence": 0.8}
message: "salut, ça va ?"
{"intent": "CHITCHAT", "language": "FRENCH", "confusion": "LOW", "complexity": 1, "confidence": 0.9}
message: "explique comment étudier les variations de x³ − 3x puis trouver ses extremums"
{"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 4, "confidence": 0.7}
message: "ma fhemtch 3lech dérivée de x² hiya 2x"
{"intent": "EXPLAIN", "language": "ARABIZI", "confusion": "HIGH", "complexity": 2, "confidence": 0.8}

## user
message: "$message"

## repair
Your reply was not valid ($error).
Return ONLY one JSON object with exactly these fields:
intent (EXPLAIN|HINT|QUIZ|ANSWER_ATTEMPT|DIAGNOSTIC|OFF_TOPIC|CHITCHAT),
language (DARIJA|ARABIZI|FRENCH|ARABIC|ENGLISH), confusion (LOW|MEDIUM|HIGH),
complexity (integer 1-5), confidence (number 0-1).
