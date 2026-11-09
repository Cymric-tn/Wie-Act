T = {
 "fr": {"pt_max": "max", "pt_min": "min", "g_title": "Courbe de f(x) = {f}", "g_tan": " et sa tangente en x = {a}", "g_der": " et sa dérivée",
        "v_title": "Tableau de variations de f(x) = {f}", "m_title": "Carte des notions", "m_focus": "Carte des notions autour de « {c} »",
        "w_clip": "Une partie de la courbe sort du cadre (échelle verticale limitée aux percentiles 2–98).",
        "err_nomatch": "Aucune règle ne correspond à cette demande. Essayez « courbe de x² », « tableau de variations de x³ − 3x » ou « carte des notions ».",
        "err_expr": "Aucune expression trouvée dans la demande.", "err_diff": "f n'est pas dérivable en x = {a} : pas de tangente.",
        "err_periodic": "Fonction périodique : donnez un domaine borné.", "err_verify": "Vérification échouée : le tableau est incohérent, il n'est pas dessiné.",
        "err_domain": "Domaine non pris en charge.", "err_solve": "Impossible de résoudre f′(x) = 0 exactement.",
        "err_focus": "Notion inconnue : {c}.", "err_course": "Fichier de cours introuvable ou vide.", "err_parse": "Je ne sais pas lire cette expression. Exemples : x^2 - 3x, (x+1)/(x-2), sqrt(x), ln(x).", "err_failed": "Impossible de dessiner cette expression."},
 "en": {"pt_max": "max", "pt_min": "min", "g_title": "Graph of f(x) = {f}", "g_tan": " and its tangent at x = {a}", "g_der": " and its derivative",
        "v_title": "Variation table of f(x) = {f}", "m_title": "Concept map", "m_focus": "Concept map around “{c}”",
        "w_clip": "Part of the curve is outside the frame (vertical scale limited to the 2nd–98th percentile).",
        "err_nomatch": "No rule matches this request. Try “plot x²”, “variation table of x³ − 3x” or “concept map”.",
        "err_expr": "No expression found in the request.", "err_diff": "f is not differentiable at x = {a}: no tangent.",
        "err_periodic": "Periodic function: give a bounded domain.", "err_verify": "Check failed: the table is inconsistent, so it is not drawn.",
        "err_domain": "Unsupported domain.", "err_solve": "Cannot solve f′(x) = 0 exactly.",
        "err_focus": "Unknown concept: {c}.", "err_course": "Course file missing or empty.", "err_parse": "I can't read this expression. Examples: x^2 - 3x, (x+1)/(x-2), sqrt(x), ln(x).", "err_failed": "This expression can't be drawn."},
 "ar": {"pt_max": "عظمى", "pt_min": "صغرى", "g_title": "منحنى الدالة f(x) = {f}", "g_tan": " ومماسه عند x = {a}", "g_der": " ومشتقتها",
        "v_title": "جدول تغيرات الدالة f(x) = {f}", "m_title": "خريطة المفاهيم", "m_focus": "خريطة المفاهيم حول «{c}»",
        "w_clip": "جزء من المنحنى خارج الإطار (المقياس العمودي محدود بين المئين 2 و98).",
        "err_nomatch": "لا توجد قاعدة تطابق هذا الطلب. جرّب «ارسم x²» أو «جدول التغيرات x³ − 3x».",
        "err_expr": "لم يتم العثور على عبارة في الطلب.", "err_diff": "الدالة غير قابلة للاشتقاق عند x = {a}: لا يوجد مماس.",
        "err_periodic": "دالة دورية: حدّد مجالاً محدوداً.", "err_verify": "فشل التحقق: الجدول غير متسق ولن يُرسم.",
        "err_domain": "مجال غير مدعوم.", "err_solve": "تعذّر حل f′(x) = 0 بدقة.",
        "err_focus": "مفهوم غير معروف: {c}.", "err_course": "ملف المقرر مفقود أو فارغ.", "err_parse": "تعذّرت قراءة هذه العبارة. أمثلة: x^2 - 3x و(x+1)/(x-2) وsqrt(x) وln(x).", "err_failed": "تعذّر رسم هذه العبارة."},
}


def t(lang: str, key: str, **kw) -> str:
    return T.get(lang, T["fr"])[key].format(**kw)
