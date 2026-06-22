from __future__ import annotations

from rad_rebuild.radiance.assembly.classification import classify_fixture_group


def _points(*pairs: tuple[float, float]) -> list[dict[str, float]]:
    return [{"x": x, "y": y, "z": 0.4572} for x, y in pairs]


def test_two_point_linear_classifies_as_linear2() -> None:
    classification = classify_fixture_group({"type": "linear2", "points": _points((0.0, 0.0), (0.5, 0.0))})

    assert classification["module_count"] == 2
    assert classification["shape"] == "linear"
    assert classification["asset_key"] == "linear2"
    assert classification["warnings"] == []


def test_three_point_collinear_classifies_as_linear3_linear() -> None:
    classification = classify_fixture_group(
        {"type": "linear3", "points": _points((0.0, 0.0), (0.5, 0.0), (1.0, 0.0))}
    )

    assert classification["asset_key"] == "linear3_linear"
    assert classification["shape"] == "linear"


def test_three_point_corner_classifies_as_linear3_corner() -> None:
    classification = classify_fixture_group(
        {"type": "linear3", "points": _points((0.0, 0.0), (0.5, 0.0), (0.5, 0.5))}
    )

    assert classification["asset_key"] == "linear3_corner"
    assert classification["shape"] == "corner"


def test_four_point_collinear_classifies_as_linear4_linear_even_when_labeled_l() -> None:
    classification = classify_fixture_group(
        {"type": "L", "points": _points((0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0))}
    )

    assert classification["asset_key"] == "linear4_linear"
    assert classification["shape"] == "linear"


def test_five_point_centerpiece_classifies_as_centerpiece() -> None:
    classification = classify_fixture_group(
        {
            "type": "centerpiece",
            "points": _points((0.0, 0.0), (0.28, -0.28), (0.28, 0.28), (-0.28, -0.28), (-0.28, 0.28)),
        }
    )

    assert classification["module_count"] == 5
    assert classification["asset_key"] == "centerpiece"
    assert classification["shape"] == "centerpiece"


def test_four_point_l_corner_classifies_as_l4_corner() -> None:
    classification = classify_fixture_group(
        {"type": "L", "points": _points((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (1.0, 0.5))}
    )

    assert classification["asset_key"] == "l4_corner"
    assert classification["shape"] == "corner"


def test_four_point_reverse_l_corner_classifies_as_l4_reverse_corner() -> None:
    classification = classify_fixture_group(
        {
            "type": "reverse_L",
            "points": _points((0.0, 0.0), (-0.5, 0.0), (-0.5, 0.5), (-1.0, 0.5)),
        }
    )

    assert classification["asset_key"] == "l4_reverse_corner"
    assert classification["shape"] == "corner"


def test_malformed_points_return_placeholder_warning() -> None:
    classification = classify_fixture_group(
        {"type": "linear3", "points": [{"x": 0.0, "y": 0.0, "z": 0.4572}, {"x": 0.0, "y": 0.0}]},
        group_index=7,
    )

    assert classification["asset_key"] == "placeholder"
    assert classification["points"] == []
    assert classification["warnings"] == ["fixture group 7 point 1 must contain finite x, y, and z numbers."]


def test_duplicate_points_return_placeholder_warning() -> None:
    classification = classify_fixture_group(
        {"type": "linear3", "points": _points((0.0, 0.0), (0.0, 0.0), (0.5, 0.0))},
        group_index=2,
    )

    assert classification["asset_key"] == "placeholder"
    assert classification["warnings"] == ["fixture group 2 contains duplicate layout points at indexes 0 and 1."]
