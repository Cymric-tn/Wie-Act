# Tableau (structure de données)

> Source: https://fr.wikipedia.org/wiki/Tableau_(structure_de_donn%C3%A9es)  
> Author: Wikipedia contributors  
> Licence: CC BY-SA 4.0 (Wikipedia)  
> Language: French · retrieved 2026-11-10 for Lumi testing

En informatique, un tableau est une structure de données représentant une séquence finie d'éléments auxquels on peut accéder efficacement par leur position, ou indice, dans la séquence. C'est un type de conteneur que l'on retrouve dans un grand nombre de langages de programmation.
Dans les langages à typage statique (comme C, Java et OCaml), tous les éléments d’un tableau doivent être du même type. Certains langages à typage dynamique (tels APL et Python) permettent des tableaux hétérogènes.

## Étymologie
Tableau, dans son sens informatique, est une traduction approximative de l'anglais array, dont on retrouve la racine dans le vieux français aroi, du verbe areer qui signifie « mettre en ordre ».

## Performances et limites
Le temps d'accès à un élément par son index est constant, quel que soit l'élément désiré. Cela s'explique par le fait que les éléments d'un tableau sont contigus dans l'espace mémoire. Ainsi, il est possible de calculer l'adresse mémoire de l'élément auquel on veut accéder, à partir de l'adresse de base du tableau et de l'index de l'élément. L'accès est immédiat, comme il le serait pour une variable simple.
Les limites d'une telle structure viennent de son avantage. Un tableau étant représenté en mémoire sous la forme de cellules contiguës, les opérations d'insertion et de suppression d'élément sont impossibles, sauf si on crée un nouveau tableau, de taille plus grande ou plus petite (selon l'opération). Il est alors nécessaire de copier tous les éléments du tableau original dans le nouveau tableau, puis de libérer l'espace mémoire alloué à l'ancien tableau. Cela fait donc beaucoup d'opérations et oblige certains langages fournissant de telles possibilités à implémenter leurs tableaux, non pas sous la forme traditionnelle (cellules adjacentes), mais en utilisant une liste chaînée, ou une combinaison des deux structures pour améliorer les performances.

## Tableau à une dimension
Avec un tableau à une dimension (aussi appelé un vecteur), un numéro d'index donne accès à un seul élément. L'image è droite, géométrique, est une représentation graphique de ce type de structure de données. Dans cet exemple, il est composé de 7 éléments.
En algorithmique, un tableau se déclare comme suit :

nomdutableau [valdebut..valfin] : Tableau de type

En langage C par exemple, on accède au premier élément d'un tableau nommé tab de la manière suivante :

tab[0];

En prenant le tableau en exemple, cette instruction retournera 45. Il est important de savoir que la numérotation de l'index commence à 0.

## Tableau à deux dimensions (ou plus)
Un tableau à deux dimensions (aussi appelé matrice) est en fait un tableau normal (à une dimension) dont les éléments sont eux-mêmes des tableaux contenant les éléments du tableau à deux dimensions. On voit ceci sur l'illustration ci-contre, où chaque élément du premier tableau (vertical à gauche) est un tableau (horizontal).
L'accès aux éléments se fait à travers deux indices, d'abord un indice pour le premier tableau, ce qui nous en renvoie un autre tableau, auquel est affecté le deuxième indice. En prenant la représentation ci-contre, l'accès à la valeur située en 2e ligne et 5e colonne (la valeur 789), se fait via les indices 1 puis 4 (rappel : on commence la numérotation des indices à zéro).
Ce mécanisme d'imbrication peut être continué pour créer des tableaux à plus de deux dimensions. On peut créer un tableau à n dimensions, l'accès aux éléments nécessite alors une série de n' indices. 
L'ordre des indices est crucial. Dans l'exemple précédent, l'élément désigné par tab[3][2] (de valeur 58) diffère de l'élément indexé par tab[2][3] (de valeur 89).

## Tableau trié
Un Tableau trié est un tableau dont les éléments sont ordonnés selon une relation d'ordre total.

## Tableau croisé dynamique
## Notes et références
## Voir aussi
## Liens externes
 Portail de la programmation informatique
