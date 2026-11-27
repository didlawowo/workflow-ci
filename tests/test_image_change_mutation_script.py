"""Contract of the image-change mutation self-test (#299)."""

from pathlib import Path
import re

SCRIPT = (Path(__file__).parents[1] / ".ci/image-change-mutation.sh").read_text()


def test_wide_timeout_bound_without_accepting_timeouts():
    multiplier = float(re.search(r"^timeout_multiplier = (\d+)", SCRIPT, re.M)[1])
    assert multiplier >= 60  # mutmut default 15 timed out on a loaded runner
    verdict = SCRIPT.split("stats = json.loads")[1]
    assert '"timeout"' in verdict and "or any(" in verdict  # timeout stays a failure
