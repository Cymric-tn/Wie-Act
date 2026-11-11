# Recherche dichotomique

> Source: https://fr.wikipedia.org/wiki/Recherche_dichotomique  
> Author: Wikipedia contributors  
> Licence: CC BY-SA 4.0 (Wikipedia)  
> Language: French · retrieved 2026-11-10 for Lumi testing

La recherche dichotomique, ou recherche par dichotomie (en anglais : binary search), est un algorithme de recherche pour trouver la position d'un élément dans un tableau trié. Le principe est le suivant : comparer l'élément avec la valeur de la case au milieu du tableau ; si les valeurs sont égales, la tâche est accomplie, sinon on recommence dans la moitié du tableau pertinente. 
Le nombre d'itérations de la procédure, c'est-à-dire le nombre de comparaisons, est logarithmique en la taille du tableau. Il y a de nombreuses structures spécialisées (comme les tables de hachage) qui peuvent être recherchées plus rapidement, mais la recherche dichotomique s'applique à plus de problèmes.

## Exemple introductif
On peut illustrer l'intérêt de la recherche dichotomique par l'exemple du jeu suivant.
A et B jouent au jeu suivant : A choisit un nombre entre 1 et 20, et ne le communique pas à B, B doit trouver ce nombre en posant des questions à A dont les réponses ne peuvent être que « Non, plus grand », « Non, plus petit » ou « Oui, trouvé ». B doit essayer de poser le moins de questions possible.
Une stratégie pour B est d'essayer tous les nombres, mais il peut aller plus rapidement comme le montre le scénario suivant :
A choisit 14 et attend les questions de B :

B sait que le nombre est entre 1 et 20 ; au milieu se trouve 10 (ou 11), B demande donc : « Est-ce que le nombre est 10 ? ». A répond « Non, plus grand ».
B sait maintenant que le nombre est entre 11 et 20 ; au milieu se trouve 15 (ou 16), il demande donc : « Est-ce que le nombre est 15 ? » A répond « Non, plus petit »
Et ainsi de suite : « Est-ce 12 ? » (12 < (11+14)÷2 < 13), « Non, plus grand », « Est-ce 13? » (13 < (13+14)÷2 < 14), « Non, plus grand », « Est-ce bien 14 ? », « Oui, trouvé »
B a trouvé le nombre choisi par A en seulement 5 questions.

## Description de l'algorithme
### Principe
L'algorithme est le suivant :

Trouver la position la plus centrale du tableau (si le tableau est vide, sortir).
Comparer la valeur de cette case à l'élément recherché.
Si la valeur est égale à l'élément, alors retourner la position, sinon reprendre la procédure dans la moitié de tableau pertinente.
On peut toujours se ramener à une moitié de tableau sur un tableau trié en ordre croissant. Si la valeur de la case est plus petite que l'élément, on continuera sur la moitié droite, c'est-à-dire sur la partie du tableau qui contient des nombres plus grands que la valeur de la case. Sinon, on continuera sur la moitié gauche.

### Algorithme et implémentation
#### Algorithme
L'algorithme suivant recherche une valeur val dans un tableau t
trié par ordre croissant, dont les cases sont numérotées de 0 à n − 1. Il renvoie un indice
de val dans le tableau, ou −1 si val est absent :

fonction recherche_dichotomique(t, val) :
    début ← 0
    fin ← taille(t) − 1
    // La condition début ≤ fin garantit l'arrêt de l'algorithme,
    // et évite une boucle infinie si val est absent du tableau.
    tant que début ≤ fin :
        mil ← partie entière((début + fin) / 2)
        si t[mil] = val :
            renvoyer mil
        sinon si t[mil] < val :
            début ← mil + 1
        sinon :
            fin ← mil − 1
    renvoyer −1

#### Implémentation
L'algorithme peut aussi s'écrire de manière récursive, comme dans
l'implémentation en Python ci-dessous. Plutôt que de découper la liste
à chaque appel, ce qui recopierait ses éléments, la fonction transmet les bornes de la zone
de recherche ; le temps d'exécution reste ainsi logarithmique.
La fonction prend en paramètres la valeur cherchée element, la liste triée
liste_triee, et les indices gauche et droite qui
délimitent la zone de recherche (par défaut, la liste entière). Elle renvoie l'indice de
element dans la liste, ou −1 s'il est absent.

### Variante pour recherche approchée
On peut modifier l'algorithme pour faire des requêtes approchées, par exemple pour trouver
la plus petite valeur du tableau strictement supérieure à une valeur donnée. Ce calcul repose sur la fonction bisect_right de la bibliothèque standard
de Python, qui renvoie la position de cette valeur dans la liste.
La fonction suivante prend en paramètres la valeur de référence element et la
liste triée liste_triee. Elle renvoie la plus petite valeur de la liste
strictement supérieure à element, ou None si aucune ne l'est.
Les indices gauche et droite délimitent la zone où peut se
trouver le résultat ; la boucle s'arrête lorsqu'ils se rejoignent.
En voici une implémentation en Python :

## Complexité et performances
La dichotomie possède une complexité algorithmique logarithmique en le nombre d'éléments composant le tableau dans lequel s'effectue la recherche.
On considère dans un premier temps le nombre de comparaisons comme étant la mesure de complexité. On appelle T(n) le nombre de comparaisons effectuées pour une instance de taille n. Alors le nombre de comparaisons T satisfait la récurrence suivante : T(n)=1+T(n/2). D'après le master theorem, cette récurrence a une solution de la forme T(n)=O(log(n)), avec la notation de Landau. Enfin le nombre de comparaisons est linéaire en le nombre d'opérations effectuées; l'algorithme a donc une complexité logarithmique.
D'autre part, pour déterminer la complexité de l'algorithme, on peut chercher à exprimer le nombre total d'opérations effectuées k (un entier naturel non nul) en fonction de la taille n de l'instance. De par le fonctionnement de la dichotomie, n est divisé par 2 à chaque itération de la boucle de recherche, la taille finale de l'instance est donc ⌊n/2k⌋(en partie entière). Puisque la plus petite taille possible d'une instance est de 1, on a 1⩽n/2k ; en multipliant par 2k (positif) on obtient 2k⩽n ; puis par composition avec le logarithme décimal (qui est croissant) k×log⁡(2)⩽log⁡(n) ; enfin en divisant par log⁡(2) non nul : k⩽log⁡(n)/log⁡(2)=log2(n). On obtient ainsi une complexité logarithmique.

## Comparaison avec d'autres méthodes
### Recherche séquentielle
La méthode de recherche la plus simple est la recherche séquentielle qui s'effectue en temps linéaire : étudier les éléments les uns après les autres. Elle ne nécessite pas d'avoir une structure de données triée. De plus elle peut être pratiquée non seulement sur un tableau, mais aussi sur une liste chaînée, qui est parfois une structure plus adaptée. Sur des tableaux ordonnés, la recherche dichotomique est plus rapide asymptotiquement, mais pas forcément sur des tableaux de petite taille.

### Hachage
Le hachage est souvent plus rapide que la recherche dichotomique, avec une complexité amortie constante. La recherche dichotomique est cependant plus robuste en ce qu'elle peut être utilisée pour d'autres tâches qu'une simple recherche, comme trouver les éléments les plus proches d'un certain élément.

### Arbre binaire de recherche
Les arbres binaires de recherche utilisent une stratégie de dichotomie similaire à celle de la recherche dichotomique. La structure est plus efficace que les tableaux triés en ce qui concerne le temps d'insertion et de suppression (logarithmique et non linéaire), mais ils prennent plus d'espace. De plus, si un tel arbre n'est pas parfaitement équilibré, alors la recherche dichotomique sur tableau sera plus rapide.

### Recherche par interpolation
Pour les tableaux triés dont les données sont régulièrement espacées, la recherche par interpolation est plus efficace.

### Autre
D'autres structures de recherche sont : les matrices Judy, les filtres de Bloom, les arbres de Van Emde Boas, arbres fusion (en), les tries et les tableaux de bits.

## Champs d'application
En dehors des considérations mathématiques, la méthode de détection de problème par dichotomie peut être appliquée à de nombreux processus.

### Test dans l'industrie
Par exemple, en industrie, si un produit passant par x phases de transformation présente une anomalie, il est très pratique d'utiliser la dichotomie pour analyser les transformations (ou processus) par groupe plutôt qu'un par un. Cela permet aussi d'effectuer des réglages précis par étape.
La méthode de dichotomie peut, par exemple, être utilisée si l'on rencontre un problème lorsque l'on groupe plusieurs appareils : on peut essayer de trouver le bon appareil en les triant et en faisant une dichotomie (en faisant des plus petits groupes).

### Recherche de zéros
La recherche par dichotomie peut être appliquée à la recherche des zéros approchés d'une fonction continue : il s'agit de la méthode de dichotomie.

## Notes et références
## Voir aussi
### Pages connexes
Diviser pour régner (informatique)
Méthode de dichotomie

### Liens externes
 Portail de l'informatique théorique
