import os

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from ask_gpt import filter_relevant_clauses


def make_clause(clause_id, precedence_level=5, plain_summary="", clause_text="", tags=None):
    return {
        "clause_id": clause_id,
        "document": "TestDoc",
        "page": 1,
        "citation": "Sec. 1",
        "clause_text": clause_text,
        "plain_summary": plain_summary,
        "link": "",
        "precedence_level": precedence_level,
        "tags": tags or [],
    }


def test_keyword_matches_are_selected():
    fence = [
        make_clause(f"FENCE_{i}", plain_summary="fence height requirements and material setback")
        for i in range(20)
    ]
    filler = [
        make_clause(f"FILLER_{i}", plain_summary="unrelated pool maintenance schedule")
        for i in range(20)
    ]
    result = filter_relevant_clauses(
        "What is the fence height requirement?", fence + filler, min_results=5, min_score=2
    )
    ids = {c["clause_id"] for c in result}
    assert all(cid.startswith("FENCE_") for cid in ids)
    assert not any(cid.startswith("FILLER_") for cid in ids)


def test_tag_match_outranks_plain_text_overlap():
    tagged = make_clause("TAGGED", plain_summary="short", tags=["fence"])
    text_heavy = make_clause("TEXTY", plain_summary="fence fence fence fence")
    padding = [make_clause(f"PAD_{i}", plain_summary="fence rule detail " + str(i)) for i in range(20)]
    result = filter_relevant_clauses(
        "fence rules", [tagged, text_heavy] + padding, min_results=3, min_score=1
    )
    assert result[0]["clause_id"] == "TAGGED"


def test_full_corpus_fallback_when_too_few_matches():
    clauses = [make_clause(f"C_{i}", plain_summary="totally unrelated filler text") for i in range(10)]
    result = filter_relevant_clauses(
        "xylophone quokka zzzznotarealword", clauses, min_results=15, min_score=2
    )
    assert result == clauses


def test_explicit_tags_param_boosts_matches():
    arc = make_clause("ARC_1", plain_summary="approval process", tags=["ARC"])
    padding = [make_clause(f"PAD_{i}", plain_summary="generic filler " + str(i)) for i in range(20)]
    result = filter_relevant_clauses(
        "random question", [arc] + padding, tags=["ARC"], min_results=3, min_score=1
    )
    assert result[0]["clause_id"] == "ARC_1"


def test_does_not_mutate_input():
    clauses = [make_clause(f"C_{i}", plain_summary="fence rules") for i in range(20)]
    snapshot = [dict(c) for c in clauses]
    filter_relevant_clauses("fence rules", clauses, min_results=5, min_score=1)
    assert clauses == snapshot


def test_sorted_by_score_then_precedence():
    a = make_clause("A", precedence_level=2, plain_summary="fence rule detail")
    b = make_clause("B", precedence_level=1, plain_summary="fence rule detail")
    padding = [make_clause(f"PAD_{i}", plain_summary="fence rule detail " + str(i)) for i in range(20)]
    result = filter_relevant_clauses("fence rule detail", [a, b] + padding, min_results=3, min_score=1)
    idx_a, idx_b = result.index(a), result.index(b)
    assert idx_b < idx_a
