"""Contract of the image-change mutation self-test (#299)."""

from pathlib import Path
import re

SCRIPT = (Path(__file__).parents[1] / ".ci/image-change-mutation.sh").read_text()


def test_wide_timeout_bound_without_accepting_timeouts():
    multiplier = float(re.search(r"^timeout_multiplier = (\d+)", SCRIPT, re.M)[1])
    assert 60 <= multiplier <= 120  # mutmut default 15 timed out on a loaded runner
    constant = float(re.search(r"^timeout_constant = (\d+)", SCRIPT, re.M)[1])
    assert 1 <= constant <= 10  # a bounded slack, never an unbounded mutant
    verdict = SCRIPT.split("stats = json.loads")[1]
    assert '"timeout"' in verdict and "or any(" in verdict  # timeout stays a failure
