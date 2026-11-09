from .spec import Diagram, DiagramError, make
from .safe_parse import parse, ParseError
from .function_graph import build_function_graph
from .variation_table import build_variation_table
from .concept_map import build_concept_map
from .detect import detect_request
from .check import check_table, solution_of
from .i18n import t

__all__ = ["Diagram", "DiagramError", "ParseError", "parse", "build_function_graph", "build_variation_table",
           "build_concept_map", "detect_request", "build_from_text", "check_table", "solution_of"]


def build_from_text(text, course_yaml=None, lang=None, force_type=None, mastery=None, practice=None):
    d = detect_request(text, course_yaml)
    lang = lang or d["lang"]
    typ = force_type or d["type"]
    if typ is None:
        raise DiagramError(t(lang, "err_nomatch"))
    if typ == "concept_map":
        if course_yaml is None:
            raise DiagramError(t(lang, "err_course"))
        return build_concept_map(course_yaml, d["focus"], mastery, lang)
    if not d["expression"]:
        raise DiagramError(t(lang, "err_expr"))
    if typ == "variation_table":
        return build_variation_table(d["expression"], lang=lang, mode="practice" if (d["practice"] if practice is None else practice) else None)
    return build_function_graph(d["expression"], d["tangent_at"], d["with_derivative"], lang=lang)
