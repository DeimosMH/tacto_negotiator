"""The deal journal's decision tree stays tied to the code it describes."""

from tacto_negotiator import deals


def test_every_branch_anchor_is_found_in_the_source():
    refs = deals.source_refs()
    for node, anchors in deals.SOURCES.items():
        assert node in deals.NODES
        assert len(refs[node]) == len(anchors), f"{node}: an anchor no longer matches its source line"
        assert all(ref["line"] > 0 for ref in refs[node])


def test_every_scripted_reason_has_a_branch():
    routed = (deals.ROOT / deals.ROUTED).read_text()
    for reason in ("terminal_offers", "mirror_alternative", "missing_quote_terms", "named_floor", "near_close_answered",
                   "simple_opening_counter", "enumerated_opening", "bounded_concession", "rfq_package_comparison",
                   "must_have_repair", "small_cost_gap", "round_reserve", "agreed_package", "quote_plateau",
                   "deadline_close", "near_close", "semantic_decision", "complex_tradeoffs"):
        assert f'"{reason}"' in routed and reason in deals.NODES


def test_paths_and_node_counts():
    steps = [{"route": "script", "reason": "bounded_concession"},
             {"route": "fast", "reason": "semantic_decision"},
             {"route": "fast", "reason": "TimeoutError", "fallback": True},
             {"route": "script", "reason": "near_close"}]
    paths = [deals.path_of(step) for step in steps]
    assert paths[0] == ["decide", "scripts", "bargain", "bounded_concession"]
    assert paths[1] == ["decide", "model", "semantic_decision", "validate"]
    assert paths[2] == ["decide", "model", "fallback", "error:TimeoutError"]
    assert paths[3] == ["decide", "scripts", "near_close"]
    counts = deals._node_steps(steps)
    assert counts["decide"] == [4, 1] and counts["model"] == [2, 2] and counts["bounded_concession"] == [1, 1]
