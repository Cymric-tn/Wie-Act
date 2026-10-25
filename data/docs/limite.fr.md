---
topic: derivative
level: beginner
language: FRENCH
title: La limite, la notion derrière la dérivée
status: draft
---
## Notion de limite
<!-- concept: limite -->
Dire que f(x) a pour limite L quand x tend vers a signifie que f(x) devient aussi proche que l'on veut de L dès que x est assez proche de a, avec x différent de a. On écrit lim f(x) = L quand x → a.

La limite ne dépend pas de la valeur de f en a : la fonction peut même ne pas être définie en a. Exemple : f(x) = (x² − 1)/(x − 1) n'est pas définie en x = 1, mais pour x ≠ 1 on a f(x) = x + 1. Donc f(x) tend vers 2 quand x tend vers 1.

## Limite et nombre dérivé
<!-- concept: nombre dérivé -->
Le nombre dérivé est une limite : f'(a) est la limite du taux d'accroissement (f(a + h) − f(a)) / h quand h tend vers 0. Exemple : pour f(x) = x², le taux d'accroissement vaut 2a + h, qui tend vers 2a, donc f'(a) = 2a.

Une limite peut ne pas exister. Quand le taux d'accroissement n'a pas de limite finie en a, la fonction n'est pas dérivable en a. C'est le cas de f(x) = √x en a = 0 : le taux vaut (√h − 0)/h = 1/√h, qui devient aussi grand que l'on veut quand h tend vers 0 par valeurs positives.
