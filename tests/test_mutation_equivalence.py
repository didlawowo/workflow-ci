"""Equivalence approval is anchored in the protected base and both content hashes."""

import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".ci"))
evidence = importlib.import_module("mutation_evidence")


@pytest.fixture
def proof(tmp_path):
    base = tmp_path / "base"
    candidate = tmp_path / "head"
    (base / ".ci").mkdir(parents=True)
    (candidate / "src").mkdir(parents=True)
    original = b"def compute(value):\n    return value + 1\n"
    mutant = "def x_compute__mutmut_1(value):\n    return value + 2\n"
    (candidate / "src/core.py").write_bytes(original)
    key = "core.x_compute__mutmut_1"
    entry = {
        "source": "src/core.py",
        "reason": "Reviewed in a separate policy change",
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "mutant_sha256": evidence.digest_function(mutant, "x_compute__mutmut_1"),
    }
    (base / ".ci/mutation-equivalents.json").write_text(json.dumps({key: entry}))
    review = {
        "base_sha": "a",
        "head_sha": "b",
        "reviewed_equivalents": [key],
        "mutants": {key: {**entry, "mutant_source": mutant}},
    }
    return base, candidate, key, review


def check(proof):
    base, candidate, key, review = proof
    return evidence.review_equivalents(
        review, {key: "survived"}, base, candidate, "a", "b"
    )


def test_review_requires_both_hashes_and_preserves_survivor(proof):
    assert check(proof) == [proof[2]]


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "mutant",
        "hash",
        "identity",
        "missing",
        "extra",
        "duplicate",
        "candidate_ledger",
    ],
)
def test_tampered_or_incomplete_equivalence_is_rejected(proof, change):
    base, candidate, key, review = proof
    if change == "source":
        (candidate / "src/core.py").write_text("def compute(value):\n    return 0\n")
    elif change == "mutant":
        review["mutants"][key]["mutant_source"] += "# irrelevant\n"
        review["mutants"][key]["mutant_source"] = review["mutants"][key][
            "mutant_source"
        ].replace("+ 2", "+ 3")
    elif change == "hash":
        review["mutants"][key]["mutant_sha256"] = "0" * 64
    elif change == "identity":
        review["head_sha"] = "stale"
    elif change == "missing":
        review.pop("mutants")
    elif change == "extra":
        review["mutants"]["other.x_f__mutmut_1"] = {}
    elif change == "duplicate":
        review["reviewed_equivalents"].append(key)
    elif change == "candidate_ledger":
        (candidate / ".ci").mkdir()
        (candidate / ".ci/mutation-equivalents.json").write_bytes(
            (base / ".ci/mutation-equivalents.json").read_bytes()
        )
        (base / ".ci/mutation-equivalents.json").write_text("{}")
    with pytest.raises(ValueError):
        check(proof)
