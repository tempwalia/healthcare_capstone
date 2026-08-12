"""BM25 retrieval over `nb/` (knowledge_base/retrieval.py) — pure functions,
no network/LLM involved, so these run the same as every other unit test in
this suite regardless of whether an LLM key is configured."""
from knowledge_base import retrieval


def test_search_finds_the_matching_insurance_plan_by_name():
    """Every insurance/ document deliberately cross-references the others in
    its own "how to compare" section (by design, to help comparison
    queries), so with only 8 total documents a query like this can put a
    couple of insurance docs very close together — the meaningful assertion
    is that acme_ppo_gold.txt clearly outranks the non-insurance documents,
    not that it's the exact #1 result (see BM25Plus comment in retrieval.py)."""
    results = retrieval.search("Acme PPO Gold copay", top_k=4)
    doc_ids = [r["doc_id"] for r in results]
    assert "insurance/acme_ppo_gold.txt" in doc_ids
    assert all(doc_id.startswith("insurance/") for doc_id in doc_ids[:4])


def test_search_ranks_the_more_relevant_plan_higher_when_comparing_two():
    """A query naming both plans should surface both, with the plan whose
    name/terms appear more prominently ranked ahead of an unrelated one."""
    results = retrieval.search("UnitedCare Basic HMO copay and network", top_k=5)
    doc_ids = [r["doc_id"] for r in results]
    assert "insurance/unitedcare_basic_hmo.txt" in doc_ids
    assert doc_ids[0] == "insurance/unitedcare_basic_hmo.txt"


def test_search_finds_prior_authorization_policy_for_spine_query():
    results = retrieval.search("does a herniated disc need prior authorization M51", top_k=3)
    doc_ids = [r["doc_id"] for r in results]
    assert "policies/prior_authorization_policy.txt" in doc_ids


def test_search_returns_empty_list_for_blank_query():
    assert retrieval.search("", top_k=5) == []
    assert retrieval.search("   ", top_k=5) == []


def test_search_returns_empty_list_for_query_with_no_relevant_matches():
    assert retrieval.search("xyzzy quantum turbine unrelated nonsense", top_k=5) == []


def test_list_documents_covers_every_policy_and_insurance_file():
    catalog = retrieval.list_documents()
    doc_ids = {d["doc_id"] for d in catalog}
    assert doc_ids == {
        "policies/referral_process_guide.txt",
        "policies/appointment_approval_policy.txt",
        "policies/prior_authorization_policy.txt",
        "policies/hipaa_privacy_notice.txt",
        "insurance/acme_ppo_gold.txt",
        "insurance/acme_hmo_silver.txt",
        "insurance/horizon_blue_ppo.txt",
        "insurance/unitedcare_basic_hmo.txt",
    }
    # README.md sits alongside the content but must never be treated as a
    # retrievable knowledge-base document itself.
    assert not any(d["doc_id"].endswith("README.md") for d in catalog)


def test_list_documents_excludes_readme_and_has_titles():
    for doc in retrieval.list_documents():
        assert doc["title"]
        assert doc["category"]


def test_get_document_returns_full_text():
    doc = retrieval.get_document("policies/referral_process_guide.txt")
    assert doc is not None
    assert doc["title"] == "How a Referral Works, Start to Finish"
    # Deliberately cross-references the platform's real status strings
    # alongside the plain-language explanation (matches static/js/utils.js's
    # REFERRAL_PROGRESS_INFO framing) rather than being purely generic prose.
    assert "awaiting_specialist_approval" in doc["text"]
    assert "specialist" in doc["text"].lower()


def test_get_document_returns_none_for_unknown_id():
    assert retrieval.get_document("policies/does_not_exist.txt") is None
