import pathlib


ENGINE = pathlib.Path(__file__).resolve().parents[1]
PLANNER_PROMPT = ENGINE / "prompts" / "planner.md"


def test_planner_prompt_requires_value_creation_and_elicitation():
    text = PLANNER_PROMPT.read_text()

    assert "create product value, not to find busywork" in text
    assert "Product-work universe" in text
    assert "Elicitation behavior" in text
    assert "Approved daily focus for this project" in text
    assert "{{DAILY_FOCUS}}" in text
    assert "Human-side feedback loop" in text
    assert "{{HUMAN_FEEDBACK}}" in text
    assert "Current loop control policy" in text
    assert "{{LOOP_CONTROL_POLICY}}" in text
    assert "Value-line behavior" in text
    assert "Do not scrape the barrel" in text
    assert "Rank by value first" in text
    assert "Safety and risk determine approval path, not ranking" in text
    assert "{{RUN_DIR}}/strategy-brief.md" in text
    assert "{{RUN_DIR}}/elicitation-questions.md" in text


def test_planner_prompt_requires_product_impact_fields():
    text = PLANNER_PROMPT.read_text()

    for field in [
        "Category:",
        "Surface:",
        "Visibility:",
        "Before:",
        "After:",
        "User benefit:",
    ]:
        assert field in text

    for json_field in [
        '"category"',
        '"surface"',
        '"visibility"',
        '"before"',
        '"after"',
        '"user_benefit"',
        '"value_score"',
    ]:
        assert json_field in text


def test_planner_prompt_requires_supervised_medium_envelope():
    text = PLANNER_PROMPT.read_text()

    assert '"auto_execute": "supervised"' in text
    assert '"requires_supervised": true' in text
    assert '"preapproved_envelope"' in text
    assert "matching `preapproved_medium_risk` envelope" in text
