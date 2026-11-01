---
topic: derivative
level: beginner
language: FRENCH
title: Dérivées du quotient, de l'inverse et de la racine carrée
status: draft
---
## Dérivée d'un quotient
<!-- concept: quotient -->
Si u et v sont dérivables et si v ne s'annule pas, alors (u/v)' = (u'·v − u·v')/v². Exemple : pour f(x) = 1/(x + 1), on pose u = 1 et v = x + 1, donc u' = 0 et v' = 1. On obtient f'(x) = (0·(x + 1) − 1·1)/(x + 1)² = −1/(x + 1)², pour x ≠ −1.

Attention au signe : le numérateur est u'·v − u·v', pas u'·v + u·v'.

## Dérivée de l'inverse
<!-- concept: inverse -->
Pour x ≠ 0, la dérivée de 1/x est −1/x² : (1/x)' = −1/x². Une constante devant se conserve : (3/x)' = 3·(1/x)' = −3/x².

Le signe moins est essentiel : la fonction 1/x est décroissante sur chaque intervalle où elle est définie, donc sa dérivée est négative.

## Dérivée de la racine carrée
<!-- concept: racine carrée -->
Pour x > 0, la dérivée de √x est 1/(2√x) : (√x)' = 1/(2√x). La fonction racine carrée n'est pas dérivable en 0. Exemple : (2√x)' = 2·1/(2√x) = 1/√x.
