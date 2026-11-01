"""rag/ingest.py: Markdown, DOCX, PDF (through a fake PyMuPDF), chunking, concept tags with confidence, OCR flags."""
import sys
import types
import zipfile

import pytest

from engine.course import load_course
from rag.chunker import estimate_text_tokens
from rag.glossary import Glossary
from rag.ingest import IngestError, ingest
from rag.retriever import VIEWER, BM25Retriever

COURSE = load_course("data/courses/analyse.yaml")
GLOSSARY = Glossary.load("data/glossary.yaml")
KW = dict(course_id="analyse", unit_id="derivees", owner="prof-7", visibility="class", source="professor",
          class_id="C1", course=COURSE, glossary=GLOSSARY)

TANGENTE = ("La tangente à la courbe au point d'abscisse a est la droite de coefficient directeur f'(a). "
            "Son équation est y = f'(a)(x − a) + f(a). La tangente donne la meilleure approximation affine.")
PRODUIT = ("Dérivée d'un produit : si u et v sont dérivables, (u·v)' = u'·v + u·v'. "
           "On ne dérive pas un produit en multipliant les dérivées.")
POLY = "Une dérivée de polynôme se calcule terme à terme avec la dérivée de la puissance et de la constante."


def md(tmp_path, text, name="cours.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------ markdown
def test_markdown_is_split_by_headings_scoped_and_tagged(tmp_path):
    f = md(tmp_path, f"# Cours de dérivation\n\n## La tangente\n{TANGENTE}\n\n## Le produit\n{PRODUIT}\n")
    res = ingest(f, **KW)
    assert [c.section for c in res.chunks] == ["La tangente", "Le produit"]
    assert [c.concept for c in res.chunks] == ["tangente", "produit"]
    c = res.chunks[0]
    assert (c.course_id, c.unit_id, c.visibility, c.owner, c.class_id, c.origin) == \
        ("analyse", "derivees", "class", "prof-7", "C1", "professor")
    assert c.chunk_id == "professor/prof-7/cours#la-tangente" and c.language == "FRENCH" and c.source == "cours.md"
    assert all(0.5 <= x.confidence <= 1.0 for x in res.chunks) and res.low_confidence == []
    assert res.needs_ocr == [] and res.warnings == []


def test_long_sections_are_packed_to_the_chunk_size(tmp_path):
    long = " ".join(f"La tangente touche la courbe au point {i}." for i in range(80))
    res = ingest(md(tmp_path, f"## Tangentes\n{long}\n"), max_tokens=120, **KW)
    assert len(res.chunks) > 3
    assert all(estimate_text_tokens(c.text) <= 120 for c in res.chunks)
    assert res.chunks[1].chunk_id.endswith("#tangentes-2")
    assert len({c.chunk_id for c in res.chunks}) == len(res.chunks)


def test_an_authors_concept_comment_is_kept_with_full_confidence(tmp_path):
    res = ingest(md(tmp_path, f"## Notes\n<!-- concept: extremum -->\n{TANGENTE}\n"), **KW)
    assert res.chunks[0].concept == "extremum" and res.chunks[0].confidence == 1.0


def test_confidence_separates_clear_ambiguous_and_off_topic_chunks(tmp_path):
    ambiguous = "Pour une somme de deux fonctions on dérive chaque terme."            # no concept title in it
    off = "La photosynthèse transforme la lumière en énergie chimique dans les feuilles."
    res = ingest(md(tmp_path, f"## A\n{TANGENTE}\n\n## B\n{POLY}\n\n## C\n{ambiguous}\n\n## D\n{off}\n"), **KW)
    by = {c.section: c for c in res.chunks}
    assert by["A"].concept == "tangente" and by["A"].confidence >= 0.5
    assert by["B"].concept in ("polynôme", "puissance", "constante") and by["B"].confidence > 0
    assert by["D"].concept == "" and by["D"].confidence == 0.0
    assert by["D"] in res.low_confidence and by["A"] not in res.low_confidence
    # two concepts matched about equally: the tag is less sure than a clear one
    assert by["B"].confidence < by["A"].confidence


def test_tags_are_restricted_to_the_unit_and_skipped_without_a_course(tmp_path):
    f = md(tmp_path, f"## A\n{TANGENTE}\n")
    assert ingest(f, **{**KW, "course": None}).chunks[0].concept == ""
    assert ingest(f, **{**KW, "course": None}).chunks[0].confidence == 1.0           # untagged, nothing to doubt
    other = ingest(f, **{**KW, "unit_id": "no-such-unit"})                            # unknown unit: whole course
    assert other.chunks[0].concept == "tangente"


def test_ids_are_stable_unique_per_owner_and_duplicate_headings_survive(tmp_path):
    f = md(tmp_path, f"## Exemple\n{TANGENTE}\n\n## Exemple\n{PRODUIT}\n")
    a1, a2 = ingest(f, **KW), ingest(f, **KW)
    assert [c.chunk_id for c in a1.chunks] == [c.chunk_id for c in a2.chunks]          # re-ingest replaces, not adds
    assert len({c.chunk_id for c in a1.chunks}) == 2
    b = ingest(f, **{**KW, "owner": "prof-8"})
    assert not {c.chunk_id for c in a1.chunks} & {c.chunk_id for c in b.chunks}


# ------------------------------------------------------------------ docx
def make_docx(path, paras):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = ""
    for style, text in paras:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>')


def test_docx_headings_become_sections(tmp_path):
    p = str(tmp_path / "poly.docx")
    make_docx(p, [("Heading1", "La tangente"), (None, TANGENTE), ("Heading2", "Le produit"), (None, PRODUIT),
                  (None, "Une seconde ligne.")])
    res = ingest(p, **KW)
    assert [c.section for c in res.chunks] == ["La tangente", "Le produit"]
    assert [c.concept for c in res.chunks] == ["tangente", "produit"] and "seconde ligne" in res.chunks[1].text
    assert res.chunks[0].origin == "professor"


def test_a_broken_docx_is_one_clear_error(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"not a zip")
    with pytest.raises(IngestError, match="not a readable .docx"):
        ingest(str(p), **KW)


# ------------------------------------------------------------------ pdf (PyMuPDF is faked: it is not installable offline)
class FakePage:
    def __init__(self, text):
        self.text = text

    def get_text(self, kind="text"):
        return self.text

    def get_pixmap(self, dpi=150):
        return types.SimpleNamespace(tobytes=lambda fmt: b"\x89PNG-fake")


class FakeDoc(list):
    def close(self):
        pass


@pytest.fixture
def fake_fitz(monkeypatch):
    pages = {}
    mod = types.ModuleType("fitz")
    mod.open = lambda path: FakeDoc(FakePage(t) for t in pages[path])
    monkeypatch.setitem(sys.modules, "fitz", mod)
    return pages


def test_pdf_pages_headings_and_scanned_pages(tmp_path, fake_fitz):
    f = tmp_path / "notes.pdf"
    f.write_bytes(b"%PDF-fake")
    fake_fitz[str(f)] = [f"1. La tangente\n{TANGENTE}\n2. Le produit\n{PRODUIT[:60]}", "", PRODUIT[60:] + "\n   ", ""]
    res = ingest(str(f), **KW)
    assert res.pages == 4 and res.needs_ocr == [2, 4]
    assert any("needs_ocr" in w for w in res.warnings)
    assert [c.section for c in res.chunks] == ["1. La tangente", "2. Le produit"]
    assert [c.page for c in res.chunks] == [1, 1]
    assert res.chunks[0].concept == "tangente"
    assert "Dérivée d'un produit" in res.chunks[1].text
    assert res.render_page(2) == b"\x89PNG-fake"


def test_a_fully_scanned_pdf_has_no_chunks_and_says_so(tmp_path, fake_fitz):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-fake")
    fake_fitz[str(f)] = ["", "  ", ""]
    res = ingest(str(f), **KW)
    assert res.chunks == [] and res.needs_ocr == [1, 2, 3] and "no text could be extracted" in res.warnings[-1]


def test_pdf_without_pymupdf_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fitz", None)
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF")
    with pytest.raises(IngestError, match="pip install pymupdf"):
        ingest(str(f), **KW)


def test_an_unopenable_pdf_is_one_clear_error(tmp_path, monkeypatch):
    mod = types.ModuleType("fitz")

    def boom(path):
        raise RuntimeError("encrypted")
    mod.open = boom
    monkeypatch.setitem(sys.modules, "fitz", mod)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF")
    with pytest.raises(IngestError, match="cannot open as a PDF"):
        ingest(str(f), **KW)


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("over,msg", [({"visibility": "secret"}, "visibility"), ({"source": "bot"}, "source"),
                                      ({"course_id": ""}, "course_id"), ({"class_id": ""}, "class_id"),
                                      ({"visibility": "private", "owner": ""}, "owner")])
def test_bad_arguments_are_rejected(tmp_path, over, msg):
    with pytest.raises(IngestError, match=msg):
        ingest(md(tmp_path, "## A\ntexte\n"), **{**KW, **over})


def test_missing_and_unsupported_files(tmp_path):
    with pytest.raises(IngestError, match="no such file"):
        ingest(str(tmp_path / "nope.md"), **KW)
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"x")
    with pytest.raises(IngestError, match="unsupported type"):
        ingest(str(p), **KW)


# ------------------------------------------------------------------ end to end with the retriever
async def test_ingested_professor_chunks_are_found_by_their_class_only_and_boosted(tmp_path):
    res = ingest(md(tmp_path, f"## La tangente\n{TANGENTE}\n"), **KW)
    from rag.chunker import chunk_dir
    r = BM25Retriever(min_coverage=0.4, professor_boost=1.3)
    await r.index(chunk_dir("data/docs") + res.chunks)
    q = "c'est quoi la tangente à une courbe ?"
    mine = {VIEWER: {"course_id": "analyse", "class_id": "C1"}}
    hits = await r.search(q, 3, mine)
    assert hits[0].chunk.origin == "professor"                      # boosted above the official docs
    stranger = await r.search(q, 3, {VIEWER: {"course_id": "analyse", "class_id": "C2"}})
    assert all(h.chunk.origin == "course" for h in stranger) and stranger


# ---------------------------------------------------------------- a real PDF (PyMuPDF installed: skipped otherwise)
def test_a_real_three_page_pdf_with_one_image_only_page(tmp_path):
    import io

    pymupdf = pytest.importorskip("pymupdf")
    from PIL import Image, ImageDraw

    doc = pymupdf.open()
    for title, body in (("1. La tangente et le nombre derive", TANGENTE), ("2. Derivee d'un produit", PRODUIT)):
        pg = doc.new_page()
        pg.insert_text((72, 90), title, fontsize=18, fontname="hebo")
        pg.insert_textbox(pymupdf.Rect(72, 120, 520, 700), body, fontsize=11, fontname="helv")
    img = Image.new("RGB", (1200, 700), "white")
    ImageDraw.Draw(img).text((60, 60), "3. Derivee d'un quotient", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc.new_page().insert_image(pymupdf.Rect(36, 36, 560, 340), stream=buf.getvalue())
    path = tmp_path / "cours.pdf"
    doc.save(str(path))
    res = ingest(str(path), "analyse", "derivees", "prof-7", "class", "professor", class_id="C1")
    assert res.pages == 3 and res.needs_ocr == [3]                      # the picture page is flagged, not guessed
    assert [c.page for c in res.chunks] == [1, 2] and all(c.course_id == "analyse" for c in res.chunks)
    assert [c.chunk_id.split("#")[1] for c in res.chunks] == ["1-la-tangente-et-le-nombre-derive", "2-derivee-d-un-produit"]
    png = res.render_page(3)                                            # the real renderer gives a PNG for the OCR
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and Image.open(io.BytesIO(png)).width > 500


def test_the_generated_pdf_page_one_at_054_is_flagged_by_the_default_cutoff(tmp_path):
    """The three-page PDF checked by hand on 2026-11-01 (page 3 is a picture): page 1's tag scores 0.54, under
    INGEST_MIN_TAG_CONFIDENCE (0.6) and over the old 0.5, so only the new default flags it."""
    from engine.course import load_courses
    from rag.glossary import Glossary
    from tests.conftest import make_settings
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for title, body in (
            ("1. La tangente et le nombre derive",
             "Le nombre derive de f en a est la limite du taux d'accroissement (f(a+h) - f(a))/h quand h tend vers 0.\n"
             "Graphiquement, f'(a) est la pente de la tangente a la courbe au point d'abscisse a.\n"
             "L'equation de la tangente en a est y = f'(a)(x - a) + f(a)."),
            ("2. Derivee d'un produit",
             "Si u et v sont derivables, la derivee du produit uv est u'v + uv'.\n"
             "Par exemple, pour f(x) = x(x + 3), on obtient f'(x) = (x + 3) + x = 2x + 3.\n"
             "Attention: la derivee d'un produit n'est pas le produit des derivees.")):
        pg = doc.new_page()
        pg.insert_text((72, 90), title, fontsize=18, fontname="hebo")
        pg.insert_textbox(pymupdf.Rect(72, 120, 520, 700), body, fontsize=11, fontname="helv")
    path = tmp_path / "cours.pdf"
    doc.save(str(path))
    default = make_settings().ingest_min_tag_confidence
    assert default == 0.6
    kw = dict(class_id="C1", course=load_courses("data/courses")["analyse"], glossary=Glossary.load("data/glossary.yaml"))
    res = ingest(str(path), "analyse", "derivees", "prof-7", "class", "professor", min_confidence=default, **kw)
    conf = {c.chunk_id.split("#")[1]: c.confidence for c in res.chunks}
    assert conf["1-la-tangente-et-le-nombre-derive"] == pytest.approx(0.54, abs=0.01)    # 0.536
    assert [c.chunk_id.split("#")[1] for c in res.low_confidence] == ["1-la-tangente-et-le-nombre-derive"]
    old = ingest(str(path), "analyse", "derivees", "prof-7", "class", "professor", min_confidence=0.5, **kw)
    assert old.low_confidence == []                                     # the old cutoff let it through
    assert make_settings(INGEST_MIN_TAG_CONFIDENCE="0.8").ingest_min_tag_confidence == 0.8
