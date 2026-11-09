"""FakeEngine.generate_page shows the same kind of content as the real engine: what the prep page's preview mode displays."""
import re

import pytest

from engine.fake import FakeEngine

CONCEPTS = ["polynôme", "produit", "puissance", "tangente"]


@pytest.fixture
def fake():
    return FakeEngine(speed=0)


@pytest.mark.parametrize("concept", CONCEPTS)
async def test_statements_are_rendered_from_the_bank_never_the_id(fake, concept):
    page = await fake.generate_page({}, {"concept_id": concept}, {"language": "FRENCH", "course_id": "analyse"})
    assert page["quiz"]
    for item in page["quiz"]:
        assert not re.search(r"\bd\d\d\b", item["statement"]), item
        assert "exemple" not in item["statement"]
        bank_item = fake.exercises.get(item["id"].split("~")[0])
        assert bank_item is not None
        if "~" not in item["id"]:
            assert item["statement"] == bank_item.render("fr")


@pytest.mark.parametrize("language,word", [("ENGLISH", "derivative"), ("ARABIC", "مشتقة")])
async def test_statements_follow_the_language(fake, language, word):
    page = await fake.generate_page({}, {"concept_id": "polynôme"}, {"language": language, "course_id": "analyse"})
    assert word in page["quiz"][0]["statement"].lower()


@pytest.mark.parametrize("concept", ["puissance", *CONCEPTS])
async def test_the_practice_item_is_never_the_worked_example(fake, concept):
    page = await fake.generate_page({}, {"concept_id": concept}, {"language": "FRENCH", "course_id": "analyse"})
    worked = page["worked_example"]
    exercise = (page["explanation_sections"] or {}).get("exercise")
    assert worked["statement"] not in page["explanation"]
    assert exercise is None or exercise["id"] not in {worked["exercise_id"], *(q["id"] for q in page["quiz"])}


async def test_a_plain_explain_still_ends_with_its_practice_item(fake):
    result = await fake.run("explain", {"question": "dérivée"}, {"language": "FRENCH"})
    assert "À toi :" in result.text and result.data["exercise"]["id"] == "d01"
