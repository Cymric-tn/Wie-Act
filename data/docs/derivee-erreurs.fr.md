---
topic: derivative
level: beginner
language: FRENCH
title: Erreurs fréquentes en dérivation
---
## Erreurs avec les puissances
<!-- concept: puissance -->
Erreur fréquente : écrire que la dérivée de x² est x, ou que la dérivée de x³ est x². La bonne règle est (xⁿ)' = n·xⁿ⁻¹ : on multiplie par l'exposant, puis on baisse l'exposant de 1. Donc (x²)' = 2x et (x³)' = 3x².

Autre piège : la dérivée de 5x² est 10x, et non 10 tout seul. On garde le x, avec l'exposant diminué de 1.

## Oublier que la dérivée d'une constante est nulle
<!-- concept: constante -->
Une constante ne varie pas, donc sa dérivée est 0. Dans f(x) = x² + 7, le 7 disparaît : f'(x) = 2x. Beaucoup d'élèves écrivent f'(x) = 2x + 7 ou f'(x) = 2x + 1, et c'est faux.

Il ne faut pas confondre une constante seule, dont la dérivée est 0, avec un terme en x comme 7x, dont la dérivée est 7.

## Erreurs avec le produit
<!-- concept: produit -->
La dérivée d'un produit n'est pas le produit des dérivées. Pour f(x) = x(x + 1), écrire f'(x) = 1 × 1 = 1 est faux. Il faut utiliser (u·v)' = u'·v + u·v', ce qui donne 2x + 1.

Une astuce pour vérifier : développer d'abord, f(x) = x² + x, puis dériver terme par terme : f'(x) = 2x + 1. Les deux méthodes doivent donner le même résultat.
