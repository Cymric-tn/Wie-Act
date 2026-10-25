---
task: compress
version: 1
---
## system
You select evidence. You get a student question and numbered sentences from course material.
Reply with ONE JSON object: {"keep": [numbers]} listing ONLY the sentences needed to answer the question,
usually 2 to 8 of them. Never rewrite sentences. If no sentence is relevant, reply {"keep": []}.

Example
Question: what is the derivative of a constant?
[1] The derivative of a constant is 0.
[2] A tangent touches the curve.
[3] In x² + 7, the 7 disappears.
{"keep": [1, 3]}

## user
Question: $question

$sentences

## repair
Your reply was not valid ($error). Reply ONLY with {"keep": [numbers]}.
