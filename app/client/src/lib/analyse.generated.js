// GENERATED from data/courses/analyse.yaml by app/scripts/gen_course.py: do not edit.
export const ANALYSE = {
 "id": "analyse",
 "titles": {
  "fr": "Analyse : dérivation",
  "en": "Calculus: differentiation",
  "ar": "التحليل: الاشتقاق"
 },
 "units": [
  {
   "id": "derivees",
   "order": 1,
   "titles": {
    "fr": "La dérivation",
    "en": "Differentiation",
    "ar": "الاشتقاق"
   }
  }
 ],
 "concepts": [
  {
   "id": "limite",
   "unit": "derivees",
   "requires": [],
   "titles": {
    "fr": "Limite",
    "en": "Limit",
    "ar": "النهاية"
   },
   "status": "draft"
  },
  {
   "id": "nombre dérivé",
   "unit": "derivees",
   "requires": [
    "limite"
   ],
   "titles": {
    "fr": "Nombre dérivé",
    "en": "Derivative at a point",
    "ar": "العدد المشتق"
   },
   "status": "published"
  },
  {
   "id": "tangente",
   "unit": "derivees",
   "requires": [
    "nombre dérivé"
   ],
   "titles": {
    "fr": "Tangente",
    "en": "Tangent line",
    "ar": "المماس"
   },
   "status": "published"
  },
  {
   "id": "constante",
   "unit": "derivees",
   "requires": [
    "nombre dérivé"
   ],
   "titles": {
    "fr": "Dérivée d'une constante",
    "en": "Derivative of a constant",
    "ar": "مشتقة دالة ثابتة"
   },
   "status": "published"
  },
  {
   "id": "puissance",
   "unit": "derivees",
   "requires": [
    "nombre dérivé"
   ],
   "titles": {
    "fr": "Dérivée d'une puissance",
    "en": "Derivative of a power",
    "ar": "مشتقة الدالة قوة"
   },
   "status": "published"
  },
  {
   "id": "polynôme",
   "unit": "derivees",
   "requires": [
    "puissance",
    "constante"
   ],
   "titles": {
    "fr": "Dérivée d'un polynôme",
    "en": "Derivative of a polynomial",
    "ar": "مشتقة كثير الحدود"
   },
   "status": "published"
  },
  {
   "id": "produit",
   "unit": "derivees",
   "requires": [
    "polynôme"
   ],
   "titles": {
    "fr": "Dérivée d'un produit",
    "en": "Derivative of a product",
    "ar": "مشتقة جداء دالتين"
   },
   "status": "published"
  },
  {
   "id": "inverse",
   "unit": "derivees",
   "requires": [
    "puissance"
   ],
   "titles": {
    "fr": "Dérivée de l'inverse",
    "en": "Derivative of the reciprocal",
    "ar": "مشتقة المقلوب"
   },
   "status": "published"
  },
  {
   "id": "quotient",
   "unit": "derivees",
   "requires": [
    "produit",
    "inverse"
   ],
   "titles": {
    "fr": "Dérivée d'un quotient",
    "en": "Derivative of a quotient",
    "ar": "مشتقة خارج قسمة"
   },
   "status": "published"
  },
  {
   "id": "racine carrée",
   "unit": "derivees",
   "requires": [
    "puissance"
   ],
   "titles": {
    "fr": "Dérivée de la racine carrée",
    "en": "Derivative of the square root",
    "ar": "مشتقة الجذر التربيعي"
   },
   "status": "published"
  },
  {
   "id": "variations",
   "unit": "derivees",
   "requires": [
    "nombre dérivé",
    "polynôme"
   ],
   "titles": {
    "fr": "Sens de variation",
    "en": "Direction of variation",
    "ar": "اتجاه تغير دالة"
   },
   "status": "published"
  },
  {
   "id": "extremum",
   "unit": "derivees",
   "requires": [
    "variations"
   ],
   "titles": {
    "fr": "Extremum",
    "en": "Extremum",
    "ar": "قيمة قصوى"
   },
   "status": "published"
  }
 ]
};
