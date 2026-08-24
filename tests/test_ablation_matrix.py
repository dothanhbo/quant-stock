from research.run_ablation_matrix import build_cases


def test_ablation_matrix_has_unique_eight_cases() -> None:
    cases = build_cases()

    assert len(cases) == 8
    assert len({case.case_id for case in cases}) == 8
    assert {case.entry for case in cases} == {
        "trend",
        "hybrid",
    }
    assert {case.exit for case in cases} == {
        "current",
        "frozen",
    }
    assert {case.sizing for case in cases} == {
        "atr_risk",
        "fixed20",
    }
