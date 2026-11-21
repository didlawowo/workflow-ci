"""Pull request file listing: Forgejo caps the page size, GitHub honours it."""

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "mutation_policy.py"
)
SPEC = importlib.util.spec_from_file_location("mutation_policy_pagination", MODULE_PATH)
assert SPEC and SPEC.loader
mutation_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutation_policy)


def event(changed_files):
    return {"number": 7, "pull_request": {"number": 7, "changed_files": changed_files}}


@pytest.fixture
def listing(monkeypatch):
    """Serve the given page sizes in order and record every requested path."""

    def install(provider, page_sizes):
        monkeypatch.setenv("POLICY_PROVIDER", provider)
        size = "limit=100" if provider == "forgejo" else "per_page=100"
        pages, start = {}, 0
        for index, count in enumerate(page_sizes, start=1):
            suffix = size if index == 1 else f"{size}&page={index}"
            pages[f"pulls/7/files?{suffix}"] = [
                {"filename": f"src/f{number}.py", "additions": 1, "deletions": 0}
                for number in range(start, start + count)
            ]
            start += count
        seen = []

        def fake_api(method, path, payload=None):
            seen.append(path)
            if method != "GET" or path not in pages:
                raise AssertionError((method, path, payload))
            return pages[path]

        monkeypatch.setattr(mutation_policy, "_api_request", fake_api)
        return seen

    return install


def test_forgejo_reads_capped_pages_until_the_counter_is_reached(listing):
    seen = listing("forgejo", [30, 30, 10])
    entries = mutation_policy._pull_request_file_entries(event(70))
    assert [entry["filename"] for entry in entries] == [
        f"src/f{number}.py" for number in range(70)
    ]
    assert seen == [
        "pulls/7/files?limit=100",
        "pulls/7/files?limit=100&page=2",
        "pulls/7/files?limit=100&page=3",
    ]


def test_forgejo_short_first_page_is_not_the_last_one(listing):
    listing("forgejo", [30, 15])
    assert len(mutation_policy._pull_request_file_entries(event(45))) == 45


def test_forgejo_incomplete_listing_fails_closed(listing):
    seen = listing("forgejo", [30, 30, 5, 0])
    assert mutation_policy._pull_request_file_entries(event(70)) is None
    assert len(seen) == 4


def test_github_pagination_is_unchanged(listing):
    seen = listing("github", [100, 1])
    assert len(mutation_policy._pull_request_file_entries(event(101))) == 101
    assert seen == ["pulls/7/files?per_page=100", "pulls/7/files?per_page=100&page=2"]


def test_github_short_page_still_ends_the_listing(listing):
    seen = listing("github", [30])
    assert mutation_policy._pull_request_file_entries(event(45)) is None
    assert seen == ["pulls/7/files?per_page=100"]


def test_forgejo_without_counter_reads_until_an_empty_page(listing):
    seen = listing("forgejo", [30, 30, 0])
    assert len(mutation_policy._pull_request_file_entries(event(None))) == 60
    assert seen[-1] == "pulls/7/files?limit=100&page=3"


def test_forgejo_zero_counter_contradicted_by_the_listing_is_ignored(listing):
    seen = listing("forgejo", [30, 5, 0])
    assert len(mutation_policy._pull_request_file_entries(event(0))) == 35
    assert len(seen) == 3


def test_listing_beyond_the_page_ceiling_fails_closed(listing):
    seen = listing("github", [100] * 101)
    assert mutation_policy._pull_request_file_entries(event(None)) is None
    assert len(seen) == 100
