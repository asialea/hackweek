from app.analysis import extract_themes


def test_extract_themes_fallback():
    snippets = [
        "I feel sad and alone",
        "There is bullying at school",
        "I feel sad about school",
    ]
    text = " \n ".join(snippets)
    themes = extract_themes(text, top_k=3)
    # themes should include 'sad' or 'school' depending on extraction
    assert any(t in themes for t in ("sad", "school"))
