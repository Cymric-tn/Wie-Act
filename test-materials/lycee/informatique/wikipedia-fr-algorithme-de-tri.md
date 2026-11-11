# Algorithme de tri

> Source: https://fr.wikipedia.org/wiki/Algorithme_de_tri  
> Author: Wikipedia contributors  
> Licence: CC BY-SA 4.0 (Wikipedia)  
> Language: French · retrieved 2026-11-10 for Lumi testing

Un algorithme de tri est, en informatique ou en mathématiques, un algorithme qui permet d'organiser une collection d'objets selon une relation d'ordre déterminée. Les objets à trier sont des éléments d'un ensemble muni d'un ordre total. Il est par exemple fréquent de trier des entiers selon la relation d'ordre usuelle « est inférieur ou égal à ». Les algorithmes de tri sont utilisés dans de très nombreuses situations. Ils sont en particulier utiles à de nombreux algorithmes plus complexes dont certains algorithmes de recherche, comme la recherche dichotomique. Ils peuvent également servir pour mettre des données sous forme canonique ou les rendre plus lisibles pour l'utilisateur.
La collection à trier est souvent donnée sous forme de tableau, afin de permettre l'accès direct aux différents éléments de la collection, ou sous forme de liste, ce qui peut se révéler être plus adapté à certains algorithmes et à l'usage de la programmation fonctionnelle.
Bon nombre d'algorithmes de tri procèdent par comparaisons successives, et peuvent donc être définis indépendamment de l'ensemble auquel appartiennent les éléments et de la relation d’ordre associée. Un même algorithme peut par exemple être utilisé pour trier des réels selon la relation d'ordre usuelle « est inférieur ou égal à » et des chaînes de caractères selon l'ordre lexicographique. Ces algorithmes se prêtent naturellement à une implémentation polymorphe.
Les algorithmes de tri sont souvent étudiés dans les cours d'algorithmique pour introduire des notions comme la complexité algorithmique ou la terminaison.
La classification des algorithmes de tri est très importante, car elle permet de choisir l’algorithme le plus adapté au problème traité, tout en tenant compte des contraintes imposées par celui-ci. Les principales caractéristiques qui permettent de différencier les algorithmes de tri, outre leur principe de fonctionnement, sont la complexité temporelle, la complexité spatiale et le caractère stable.

## Principe de fonctionnement
On distingue les algorithmes procédant par comparaisons successives entre éléments, dits « tris par comparaisons », des algorithmes plus spécialisés faisant des hypothèses restrictives sur la structure des données à trier (par exemple, le tri par comptage, applicable uniquement si les données sont prises dans un ensemble borné connu à l'avance).
Les algorithmes de tri par comparaison lisent les entrées uniquement au moyen d'une fonction de comparaison binaire ou ternaire (lorsque le cas d'égalité est traité différemment). Il existe encore différents principes de fonctionnement au sein de cette classe : certains algorithmes de tri par comparaison procèdent par insertions successives, d'autres par fusions, d'autres encore par sélection.
En l'absence de précisions, on entend habituellement par « algorithme de tri » un algorithme de tri procédant par comparaisons.

## Complexité algorithmique
### Analyse de la complexité des algorithmes
La complexité temporelle (en moyenne ou dans le pire des cas) mesure le nombre d'opérations élémentaires effectuées pour trier une collection d'éléments. C'est un critère majeur pour comparer les algorithmes de tri, puisque c'est une estimation directe du temps d'exécution de l'algorithme. Dans le cas des algorithmes de tri par comparaison, la complexité en temps est le plus souvent assimilable au nombre de comparaisons effectuées, la comparaison et l'échange éventuel de deux valeurs s'effectuant en temps constant.
La complexité spatiale (en moyenne ou dans le pire des cas) représente, quant à elle, la quantité de mémoire dont va avoir besoin l'algorithme pour s'exécuter. Celle-ci peut dépendre, comme le temps d'exécution, de la taille de l'entrée. Il est fréquent que les complexités spatiales en moyenne et dans le pire des cas soient identiques. C'est souvent implicitement le cas lorsqu’une complexité est donnée sans indication supplémentaire.
La complexité en temps est souvent notée T (ou C)et  est exprimée comme une fonction du nombre n d'éléments à trier à l'aide des notations de Landau 𝒪, Ω et Θ.

### Résultats sur les algorithmes de tris
Certains algorithmes de tri simples ont une complexité en temps quadratique, i.e. T(n)=𝒪(n2), tandis que d'autres, plus élaborés, ont une complexité quasi linéaire : T(n)=𝒪(nlog⁡(n)).
La complexité temporelle en moyenne d’un algorithme basé sur une fonction de comparaison ne peut pas être meilleure que 𝒪(nlog⁡(n)). Les tris qui ne demandent que 𝒪(nlog⁡(n)) comparaisons en moyenne sont par conséquent dits optimaux. Ce résultat constitue une borne inférieure asymptotique, mais on montre également que le nombre exact de comparaisons nécessaires est minoré par ⌈log2(n!)⌉.

Pour certains types de données (entiers, chaînes de caractères de taille bornée), il existe cependant des algorithmes plus efficaces au niveau du temps d'exécution, comme le tri comptage ou le tri par base. Ces algorithmes n'utilisent pas la comparaison entre éléments (la borne Ω(n×log(n)) ne s'applique donc pas pour eux) mais nécessitent des hypothèses sur les objets à trier. Par exemple, le tri comptage et le tri par base s'appliquent à des entiers que l'on sait appartenir à l'ensemble [1, m] avec comme hypothèse supplémentaire pour le tri par base que m soit une puissance de 2 (c’est-à-dire de la forme 2k).

## Critères de classification
### Tri en place
Un tri est dit en place s'il n'utilise qu'un nombre très limité de variables et qu’il modifie directement la structure qu’il est en train de trier. Ceci nécessite l’utilisation d'une structure de donnée adaptée (un tableau par exemple). Ce caractère peut être très important si on ne dispose pas de beaucoup de mémoire.
Toutefois, on ne déplace pas, en général, les données elles-mêmes, mais on modifie seulement des références (ou pointeurs) vers ces dernières.

### Tri stable
Un tri est dit stable s'il préserve l’ordonnancement initial des éléments que l'ordre considère comme égaux. Pour définir cette notion, il est nécessaire que la collection à trier soit ordonnancée d'une certaine manière (ce qui est souvent le cas pour beaucoup de structures de données, par exemple pour les listes ou les tableaux).

Les algorithmes de tri instables peuvent être retravaillés spécifiquement afin de les rendre stables, cependant cela peut être aux dépens de la rapidité et/ou peut nécessiter un espace mémoire supplémentaire.
Parmi les algorithmes listés plus bas, les tris stables sont : le tri à bulles, le tri par insertion et le tri fusion. Les autres algorithmes nécessitent O(n) mémoire supplémentaire pour stocker l'ordre initial des éléments.

### Tri interne et externe
Un tri interne s'effectue entièrement en mémoire centrale (RAM) tandis qu'un tri externe utilise des fichiers sur une mémoire de masse pour trier des volumes trop importants pour pouvoir tenir en mémoire centrale. Certains types de tris, comme le tri fusion ou les tris par distribution, s'adaptent facilement à l'utilisation de mémoire externe. D'autres algorithmes, à l'inverse, accèdent aux données de telle sorte qu'ils ne se prêtent pas à cet usage car cela nécessiterait d'effectuer constamment des lectures/écritures entre les mémoires principales et externes.

### Tri parallèle
Certains algorithmes permettent d'exploiter les capacités multitâches de la machine. Notons également que certains algorithmes, notamment ceux qui fonctionnent par insertion, peuvent être lancés sans connaître l'intégralité des données à trier ; on peut alors trier et produire les données à trier en parallèle.

## Comparaison des algorithmes
Le tableau ci-dessous permet de comparer différents algorithmes de tri procédant par comparaisons. n y représente le nombre d'éléments à trier. Toutes les complexités doivent être interprétées à l'aide d'un grand O de Landau. Il est supposé que les opérations élémentaires comme les comparaisons et les échanges peuvent être effectués en temps constant.

## Exemples d'algorithmes de tri
### Tris par comparaison
#### Algorithmes rapides
Tri fusion (merge sort) − T(n)=𝒪(nlog⁡n) dans tous les cas ; stable ; pas en place par défaut.
Cet algorithme repose sur le principe « diviser pour régner ». Pour une entrée donnée, l'algorithme la divise en deux parties de tailles similaires, trie chacune d'entre elles en utilisant le même algorithme, puis fusionne les deux parties triées. Il se prête aussi bien à des implémentations sur listes que sur tableaux. Il est utilisé en particulier par l'algorithme hybride Timsort.
Tri rapide (quicksort) − T(n)=𝒪(nlog⁡n) en moyenne et dans le meilleur des cas, T(n)=𝒪(n2) dans le pire des cas ; instable ; en place dans la variante de Sedgewick.
Cette méthode repose sur le principe « diviser pour régner ». Une valeur est choisie comme pivot et les éléments plus petits que le pivot sont dissociés, par échanges successifs, des éléments plus grands que le pivot ; chacun de ces deux sous-ensembles est ensuite trié de la même manière. On peut rendre la complexité quasiment indépendante des données en utilisant un pivot aléatoire ou en appliquant au tableau une permutation aléatoire avant de le trier.
Tri par tas (heap sort) − T(n)=𝒪(nlog⁡n) dans tous les cas ; en place mais instable.
Il s'agit d'une amélioration du tri par sélection. L'idée est la même (insérer les éléments un à un dans une structure déjà triée), mais l'algorithme utilise une structure de tas, souvent implémentée au moyen d'un tableau. Cet algorithme fonctionne très bien en conjonction avec le tri rapide. En effet, il est efficace quand on soupçonne que les données sont proches du pire cas (quadratique) du tri rapide.[pas clair]
Introsort − T(n)=𝒪(nlog⁡n) dans tous les cas ; instable mais en place.
Il s'agit d'un hybride du tri rapide et du tri par tas.
Tri arborescent − T(n)=𝒪(nlog⁡n) en moyenne, T(n)=𝒪(n2) dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas. ; ni stable ni en place.
L'idée est d'insérer les éléments un à un dans un arbre binaire de recherche, puis de lire l'arbre selon un parcours en profondeur. Ce tri est un des plus lents (parmi les tris rapides) et un des plus gourmands en mémoire à cause de la structure d'arbre binaire à manipuler. Il est possible de le rendre quasi linéaire dans tous les cas[Comment ?] en maintenant un arbre équilibré (c.f. Arbre AVL).
Smoothsort − T(n)=𝒪(nlog⁡n) en moyenne et dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas ; en place mais instable.
Tri inspiré du tri par tas, mais qui utilise un arbre non inversé[Quoi ?]. Ce tri est très rapide pour les ensembles déjà presque triés.
Tri à peigne (comb sort) − T(n)=𝒪(n) dans le meilleur des cas, T(n)=𝒪(nlog⁡n) en moyenne et T(n)=𝒪(n2) dans le pire des cas ; instable mais en place.
Il s'agit d'une variante plus efficace du tri à bulles, ne comparant pas uniquement des éléments consécutifs. On peut dire qu'il est au tri à bulles ce que le tri de Shell est au tri par insertion.
Pour un algorithme de tri donné instable, il est facile d'en obtenir une variante stable en utilisant un tableau supplémentaire pour mémoriser l'ordre initial des éléments. L'algorithme obtenu n'est toutefois pas en place.

#### Algorithmes moyennement rapides
Tri de Shell (shell sort) − T(n)=𝒪(nlog2n) pour la série de pas 2p3q et T(n)=𝒪(n3/2) pour la série de pas 2k−1 ; instable ; en place.
Ce tri repose sur le tri par insertion des sous-suites de l'entrée obtenues en prenant les éléments espacés d'un pas constant, pour une suite de pas prédéfinie. La complexité varie selon le choix de cette suite. On ne connaît pas de série donnant  O(nlog⁡n).
Pour un algorithme de tri donné instable, il est facile d'en obtenir une variante stable en utilisant un tableau supplémentaire pour mémoriser l'ordre initial des éléments. Il peut être rendu stable, mais de préférence en travaillant sur des listes.

Tri par insertion − T(n)=𝒪(n2) en moyenne et dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas ; stable et en place.
C'est le tri souvent utilisé naturellement pour trier des cartes à jouer : les valeurs sont insérées les unes après les autres dans une liste triée (initialement vide). C'est souvent le plus rapide et le plus utilisé pour trier des entrées de petite taille. Il est également efficace pour des entrées déjà presque triées.
Tri à bulles − T(n)=𝒪(n2) en moyenne et dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas ; stable et en place.
L'algorithme consiste à parcourir l'entrée du début à la fin et, pour chaque couple d'éléments consécutifs, à les intervertir s'ils sont mal ordonnés. Cette opération est répétée jusqu'à ce que la structure soit triée (aucune interversion lors du dernier passage). Cet algorithme est peu efficace et rarement utilisé en pratique ; son intérêt est principalement pédagogique.
Tri cocktail − T(n)=𝒪(n2) en moyenne et dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas ; stable et en place.
Il s'agit d'une variante du tri à bulles dans laquelle l'entrée est alternativement parcourue dans les deux sens. S'il permet de traiter de manière plus efficace quelques cas problématiques pour le tri à bulles, il reste essentiellement similaire à ce dernier et l'intérêt est encore une fois principalement pédagogique.
Tri pair-impair − T(n)=𝒪(n2) en moyenne et dans le pire des cas, T(n)=𝒪(n) dans le meilleur des cas ; stable et en place.
Il s'agit d'une variante du tri à bulles, qui procède en comparant successivement tous les éléments d'index pairs avec les éléments d'index impairs qui les suivent, puis inversement. On va ainsi commencer en comparant le premier élément au second, le troisième au quatrième, etc., puis l'on comparera le second élément au troisième, le quatrième au cinquième etc. L'opération est répétée jusqu'à ce que la structure soit triée.

#### Algorithmes lents
Ces algorithmes ont une complexité asymptotique en 𝒪(n2) et sont par conséquent considérés comme lents pour des entrées dont la taille est de plus de quelques dizaines d'éléments.

Tri par sélection − T(n)=𝒪(n2) dans tous les cas ; sur place ; instable par défaut (peut être rendu stable, mais de préférence en travaillant sur des listes).
Il s'agit, à chaque itération, d'identifier le plus petit des éléments qui ne sont pas encore triés, et de l'échanger avec le premier de ceux-ci. Ce tri est rapide pour des petites entrées, et se code de manière concise.

#### Algorithmes très lents
Ces algorithmes ont une complexité asymptotique moins bonne que 𝒪(n2), qui est la complexité des algorithmes les plus intuitifs.

Tri stupide (bogosort) − Ne termine pas dans le pire des cas, T(n)=𝒪(n!) en moyenne et T(n)=𝒪(n) dans le meilleur des cas ; instable mais en place.
Le tri stupide consiste à vérifier si les éléments sont ordonnés et s'ils ne le sont pas à mélanger aléatoirement les éléments, puis à répéter l'opération.
Tri faire-valoir (stooge sort) - T(n)=𝒪(nlog⁡3log⁡1,5) soit approximativement 𝒪(n2,71) ; instable et pas en place[réf. nécessaire].
Ce tri consiste à échanger si nécessaire le premier et le dernier élément, puis à trier récursivement les deux premiers tiers, puis les deux derniers, puis de nouveau les deux premiers du tableau.

### Tris utilisant la structure des données
Tri comptage ou tri par dénombrement (counting sort) : Algorithme linéaire, T(n)=𝒪(n), stable mais nécessite l'utilisation d'une seconde liste de même longueur que la liste à trier. Son utilisation relève de la condition que les valeurs à trier sont des entiers naturels dont on connaît les extrema ;
Tri par base (radix sort) : c'est aussi un tri linéaire dans certaines conditions (moins restrictives que pour le tri par comptage), T(n)=𝒪(n), stable mais nécessite aussi l'utilisation d'une seconde liste de même longueur que la liste à trier ;
Tri par paquets (bucket sort) : Stable et en complexité linéaire -- T(n)=𝒪(n), part de l'hypothèse que les données à trier sont réparties de manière uniforme sur un intervalle réel [a,b[.

### Tris externes
Les algorithmes de tri doivent aussi être adaptés en fonction des configurations informatiques sur lesquelles ils sont utilisés. Dans les exemples cités plus haut, on suppose que toutes les données sont présentes en mémoire centrale (ou accessibles en mémoire virtuelle). La situation se complexifie si l'on veut trier des volumes de données supérieurs à la mémoire centrale disponible (ou si l'on cherche à améliorer le tri en optimisant l'utilisation de la hiérarchie de mémoire).
Ces algorithmes sont souvent basés sur une approche assez voisine de celle du tri fusion. Le principe est le suivant :

découpage du volume de données à trier en sous-ensembles de taille inférieure à la mémoire rapide disponible ;
tri de chaque sous-ensemble en mémoire centrale pour former des « monotonies » (sous-ensembles triés) ;
interclassement des monotonies.

## Choix empirique d'un algorithme de tri
Beaucoup d'algorithmes existent, mais certains sont bien plus utilisés que d'autres en pratique. Le tri par insertion est souvent plébiscité pour des données de petite taille, tandis que des algorithmes asymptotiquement efficaces, comme le tri fusion, le tri par tas ou quicksort, seront utilisés pour des données de plus grande taille.
Il existe des implémentations finement optimisées, qui sont souvent des algorithmes hybrides. Timsort utilise ainsi à la fois les méthodes de tri fusion et de tri par insertion, et est utilisé entre autres par Android, Java et Python ; Introsort, qui combine quicksort et tri par tas, est utilisé dans certaines implémentations du tri C++.
La comparaison empirique d'algorithmes n'est pas aisée dans la mesure où beaucoup de paramètres entrent en compte : taille de données, ordre des données, matériel utilisé, taille de la mémoire vive, etc. Par exemple, les essais effectués sur des données tirées aléatoirement ne représentent pas forcément très fidèlement les comportements obtenus avec des données réelles.

### Accès à la mémoire vive
Afin de comparer différents algorithmes, il est important de prendre en compte la taille des données à trier ainsi que la quantité de mémoire vive disponible. Lorsqu'il n'y a plus assez de mémoire vive pour stocker les données, l'ordinateur aura recours à l'usage de mémoire externe, ce qui résulte en des temps d'accès nettement plus longs.
Dans cette situation, les algorithmes qui travaillent successivement sur des parties de plus petites tailles de l'entrée (qui seront par exemple fusionnées par la suite) auront tendance à mieux fonctionner que des algorithmes comme quicksort qui effectueront plus d'accès à la mémoire externe.
Il est également possible d'éviter de telles situations, par exemple en associant aux données à trier des clés plus petites, et en triant directement ces clés en mémoire vive. Lorsque la taille des données est vraiment importante, un algorithme de tri externe sera utilisé afin de minimiser le nombre d'accès à la mémoire externe.

## Problèmes liés
Parmi les problèmes proches du tri, on peut mentionner le tri partiel (en), qui consiste, pour k fixé, à trier les k plus petits éléments, ou le problème de sélection, qui consiste à trouver le k-ième plus petit élément de l'entrée. Bien que trier l'entrée en intégralité permette de résoudre ces problèmes, il existe des solutions plus subtiles et moins coûteuses. C'est le cas par exemple de quickselect, qui possède des similitudes avec le tri rapide.
À l'inverse, on peut chercher à construire des algorithmes qui mélangent de manière aléatoire l'entrée qui leur est donnée ; c'est le cas par exemple du mélange de Fisher-Yates.
Un autre problème est de trier un tableau qui est déjà presque trié (c'est le cas avec les mégadonnées où les algorithmes conventionnels sont disqualifiés). Cela peut réhabiliter des algorithmes comme le tri par insertion.

## Histoire
La création de la première routine de tri est attribuée à Betty Holberton, lors de la seconde guerre mondiale.

## Annexes
### Notes et références
### Bibliographie
Thomas H. Cormen, Charles E. Leiserson, Ronald L. Rivest et Clifford Stein (trad. de l'anglais), Algorithmique : Cours avec 957 exercices et 158 problèmes, Dunod, 2010 [détail de l’édition]
Christine Froidevaux, Marie-Claude Gaudel et Michèle Soria, Types de données et algorithmes, McGraw-Hill, 1990, 577 p. (ISBN 978-2-7042-1217-0, BNF 35070753, lire en ligne), chap. 16

### Liens externes
Mémoire de synthèse sur les algorithmes de tri
Dossier sur les algorithmes de tri et leur complexité (et implémentation en divers langages)
Illustration dynamique de plusieurs tris (nécessite Java)
(en) Illustration dynamique de plusieurs tris 
(en) « Sorting Algorithms Visualized », Visualisation de différents tris à l’aide de couleurs
Sur le site Interstices, document sur les algorithmes de tri avec une applet Java
 Portail de l'informatique théorique
