from research.run_regime_entry_diagnostic import build_cases, build_policy


def test_regime_diagnostic_has_current_and_predeclared_bull_bracket():
    cases = build_cases()
    assert len(cases) == 5
    assert sum(case.is_current_production for case in cases) == 1
    assert {case.bull_max_positions for case in cases} == {0, 1, 3, 5}


def test_sideway_only_blocks_bull_but_keeps_sideway_entries():
    case = next(case for case in build_cases() if case.case_id.startswith("sideway_only"))
    policy = build_policy(case)
    assert not policy.resolve("BULL").allow_new_positions
    assert policy.resolve("SIDEWAY").allow_new_positions
    assert not policy.resolve("BEAR").allow_new_positions
