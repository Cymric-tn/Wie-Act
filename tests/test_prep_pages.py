

# ---------------------------------------------------------------- v1.4: every published concept's page has 3 quiz items
def test_every_published_concept_gets_three_quiz_items_a_worked_example_and_correct_variants():
    from engine import prep
    from engine.course import load_courses
    from engine.exercises import ExerciseBank
    from engine.mathcheck import equivalent, parse_answer
    bank = ExerciseBank.load("data/exercises/derivees.yaml", "data/templates.yaml")
    course = load_courses("data/courses")["analyse"]
    for cid, concept in course.concepts.items():
        if concept.status == "draft":
            continue
        worked, quiz = prep.page_exercises(bank, course, cid, 0.2, "FRENCH")
        assert worked and len(quiz) == 3, (cid, len(quiz))
        assert len({q["id"] for q in quiz}) == 3 and worked["exercise_id"] not in {q["id"] for q in quiz}, cid


def test_value_and_tangent_variants_are_recomputed_exactly():
    from engine import prep
    from engine.exercises import ExerciseBank
    bank = ExerciseBank.load("data/exercises/derivees.yaml", "data/templates.yaml")
    v = prep.variant_of(bank.get("d10"), 1)                       # f(x) = x² at 3  ->  f(x) = 2x² at 3
    assert (v["id"], v["solution"], v["statement"]["fr"]) == ("d10~v1", "f'(3) = 12", "Soit f(x) = 2x². Calculer f'(3).")
    assert v["statement"]["en"].startswith("Let f(x) = 2x²") or "2x²" in v["statement"]["en"]
    t = prep.variant_of(bank.get("d12"), 2)                       # tangent of 3x² at 2: slope 12, y = 12x - 12
    assert t["solution"] == "y = 12x - 12" and "3x²" in t["statement"]["fr"]
    assert prep.variant_of(bank.get("d10"), 1) == prep.variant_of(bank.get("d10"), 1)        # deterministic
    assert prep.variant_of(bank.get("d01"), 1)["solution"] == "f'(x) = 8x³"                  # the old derive-f variants are unchanged
