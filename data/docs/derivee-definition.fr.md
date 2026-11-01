---
topic: derivative
course: analyse
unit: derivees
level: beginner
language: FRENCH
title: La dérivée d'une fonction : définition
---
## Idée intuitive
<!-- concept: nombre dérivé -->
La dérivée mesure la vitesse à laquelle une fonction change. Si f(t) est la distance parcourue par une voiture au temps t, la dérivée f'(t) est sa vitesse instantanée : de combien la distance augmente pendant un tout petit intervalle de temps.

Une dérivée positive signifie que la fonction augmente à cet endroit, une dérivée négative qu'elle diminue, et une dérivée nulle que la fonction est momentanément « à plat ». On note la dérivée de f par f' (on lit « f prime »). Calculer une dérivée s'appelle dériver.

## Nombre dérivé et taux d'accroissement
<!-- concept: nombre dérivé -->
Pour une fonction f et un nombre a, le taux d'accroissement entre a et a + h est le quotient (f(a + h) − f(a)) / h. C'est la pente de la droite qui passe par les deux points de la courbe d'abscisses a et a + h.

Quand h devient de plus en plus petit (h tend vers 0), si ce quotient se rapproche d'un nombre fini, ce nombre s'appelle le nombre dérivé de f en a et se note f'(a). Exemple : pour f(x) = x², le taux d'accroissement vaut (2ah + h²) / h = 2a + h, qui tend vers 2a quand h tend vers 0. Donc f'(a) = 2a.

## Interprétation graphique : la tangente
<!-- concept: tangente -->
Le nombre dérivé f'(a) est le coefficient directeur, c'est-à-dire la pente, de la tangente à la courbe de f au point d'abscisse a. La tangente est la droite qui touche la courbe en ce point en suivant sa direction.

L'équation de la tangente au point d'abscisse a est y = f'(a)(x − a) + f(a). Exemple : pour f(x) = x² en a = 1, on a f(1) = 1 et f'(1) = 2, donc la tangente a pour équation y = 2(x − 1) + 1, c'est-à-dire y = 2x − 1.
