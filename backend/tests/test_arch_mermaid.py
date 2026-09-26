"""Tests for app/architecture/mermaid.py — sanitization, generation, validation."""

from app.architecture import mermaid as mm
from app.models.contracts import ArchitectureComponent, ArchitectureRelationship


def _component(id_, label, type_="service", rank=1.0) -> ArchitectureComponent:
    return ArchitectureComponent(
        id=id_, label=label, type=type_, confidence=0.8, rank=rank
    )


def _rel(src, dst, type_="imports") -> ArchitectureRelationship:
    return ArchitectureRelationship(source=src, target=dst, type=type_, confidence=0.7)


# ── label sanitization ────────────────────────────────────────────────────────


def test_safe_label_strips_forbidden_characters():
    assert (
        mm._safe_label("a<b>c&d{e}f[g]h|i\\j`k;l%m#n$o@p~q^r\"s't")
        == "abcdefghijklmnopqrst"
    )


def test_safe_label_collapses_whitespace_left_by_removed_chars():
    # '&' is stripped; the two spaces it leaves behind must collapse to one.
    assert mm._safe_label("API & Routes") == "API Routes"


def test_safe_label_strips_control_and_zero_width_chars():
    assert mm._safe_label("a\u200bb\x00c") == "abc"


def test_safe_label_truncates_long_text():
    label = mm._safe_label("x" * 100, max_len=10)
    assert len(label) == 10
    assert label.endswith("…")


def test_safe_label_empty_falls_back():
    assert mm._safe_label("") == "unlabeled"
    assert mm._safe_label("<<<>>>") == "unlabeled"


def test_safe_label_never_raises_on_hostile_input():
    hostile = '<script>alert(1)</script>"; DROP TABLE x; --\u202e\u200f'
    label = mm._safe_label(hostile)
    assert "<" not in label and ">" not in label and '"' not in label


# ── generation ────────────────────────────────────────────────────────────────


def test_generate_from_parts_empty_components_returns_empty_string():
    assert mm.generate_from_parts([], []) == ""


def test_generate_uses_synthetic_node_ids_not_component_ids():
    comps = [_component("weird id; DROP TABLE", "Weird")]
    text = mm.generate_from_parts(comps, [])
    assert "weird id" not in text
    assert "n0" in text


def test_generate_uses_cylinder_shape_for_data_store():
    comps = [_component("db", "Database", type_="data_store")]
    text = mm.generate_from_parts(comps, [])
    assert '[("Database")]' in text


def test_generate_is_a_pure_function():
    comps = [_component("a", "A"), _component("b", "B", rank=0.5)]
    rels = [_rel("a", "b")]
    assert mm.generate_from_parts(comps, rels) == mm.generate_from_parts(comps, rels)


def test_generate_output_is_stable_regardless_of_input_order():
    comps_a = [_component("a", "A", rank=1.0), _component("b", "B", rank=0.5)]
    comps_b = [_component("b", "B", rank=0.5), _component("a", "A", rank=1.0)]
    assert mm.generate_from_parts(comps_a, []) == mm.generate_from_parts(comps_b, [])


def test_generate_drops_edge_with_unknown_endpoint():
    comps = [_component("a", "A")]
    rels = [_rel("a", "does-not-exist")]
    text = mm.generate_from_parts(comps, rels)
    assert "-->" not in text


def test_generate_respects_node_cap(monkeypatch):
    monkeypatch.setattr(mm, "ARCH_MAX_MERMAID_NODES", 2)
    comps = [_component(f"c{i}", f"Comp {i}", rank=float(10 - i)) for i in range(5)]
    text = mm.generate_from_parts(comps, [])
    assert text.count('["Comp') == 2
    assert "more component" in text


def test_generate_shrinks_to_fit_char_budget(monkeypatch):
    monkeypatch.setattr(mm, "ARCH_MAX_MERMAID_CHARS", 120)
    comps = [
        _component(f"c{i}", f"Component number {i}", rank=float(10 - i))
        for i in range(20)
    ]
    text = mm.generate_from_parts(comps, [])
    assert (
        len(text) <= 250
    )  # shrink loop keeps trying; final guard is a single node minimum
    assert text.startswith("graph TD")


# ── validate() ────────────────────────────────────────────────────────────────


def test_validate_accepts_its_own_generated_output():
    comps = [_component("a", "A"), _component("b", "B", type_="data_store")]
    rels = [_rel("a", "b", "calls")]
    text = mm.generate_from_parts(comps, rels)
    assert mm.validate(text) == []


def test_validate_rejects_click_directive():
    assert (
        mm.validate('graph TD\n    n0["x"]\n    click n0 "http://evil.example"') != []
    )


def test_validate_rejects_style_directives():
    assert mm.validate('graph TD\n    n0["x"]\n    classDef bad fill:#fff') != []
    assert mm.validate('graph TD\n    n0["x"]\n    linkStyle 0 stroke:red') != []


def test_validate_rejects_init_directive():
    assert (
        mm.validate('%%{init: {"securityLevel": "loose"}}%%\ngraph TD\n    n0["x"]')
        != []
    )


def test_validate_rejects_html_in_label():
    assert mm.validate('graph TD\n    n0["<img src=x onerror=alert(1)>"]') != []


def test_validate_rejects_unrecognised_syntax():
    assert mm.validate('graph TD\n    n0["x"]\n    n0 ~~~ n1') != []


def test_validate_accepts_empty_text():
    assert mm.validate("") == []
    assert mm.validate("   \n  \n") == []


def test_validate_accepts_curly_amp_and_middot_inside_labels():
    """These are legitimate data characters (present in the curated OptiLearn
    diagram) — only structural Mermaid metacharacters are forbidden."""
    text = 'graph TD\n    n0["Translate  Learn · TTS"]'
    assert mm.validate(text) == []
