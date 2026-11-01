---
topic: derivative
course: analyse
unit: derivees
level: beginner
language: FRENCH
title: Formules de dérivation
---
## Dérivées des fonctions usuelles
<!-- concept: puissance -->
- La dérivée d'une constante est 0 : si f(x) = 5, alors f'(x) = 0.
- Si f(x) = x, alors f'(x) = 1.
- Si f(x) = x², alors f'(x) = 2x.
- Plus généralement, si f(x) = xⁿ avec n entier supérieur ou égal à 1, alors f'(x) = n·xⁿ⁻¹. Exemple : la dérivée de x³ est 3x².
- Si f(x) = 1/x pour x différent de 0, alors f'(x) = −1/x².
- Si f(x) = √x (racine carrée de x) pour x > 0, alors f'(x) = 1/(2√x).

## Somme et produit par une constante
<!-- concept: polynôme -->
La dérivée d'une somme est la somme des dérivées : (u + v)' = u' + v'. Multiplier une fonction par une constante k multiplie sa dérivée par k : (k·u)' = k·u'.

Ces deux règles permettent de dériver tous les polynômes terme par terme. Exemple : f(x) = 3x² + 5x − 7 donne f'(x) = 3 × 2x + 5 × 1 − 0 = 6x + 5.

## Produit et quotient
<!-- concept: produit -->
Pour un produit de deux fonctions u et v : (u·v)' = u'·v + u·v'. Ce n'est pas u'·v'. Exemple : f(x) = x(x + 1) avec u = x et v = x + 1 donne f'(x) = 1·(x + 1) + x·1 = 2x + 1.

Pour un quotient, quand v ne s'annule pas : (u/v)' = (u'·v − u·v') / v². Exemple : f(x) = 1/(x + 2) donne f'(x) = (0·(x + 2) − 1·1) / (x + 2)² = −1/(x + 2)².
