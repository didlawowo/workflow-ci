"""Contract of the image-change mutation self-test (#299)."""

from pathlib import Path
import re

SCRIPT = (Path(__file__).parents[1] / ".ci/image-change-mutation.sh").read_text()


def test_wide_timeout_bound_without_accepting_timeouts():
    multiplier = float(re.search(r"^timeout_multiplier = (\d+)", SCRIPT, re.M)[1])
    constant = float(re.search(r"^timeout_constant = (\d+)", SCRIPT, re.M)[1])
    assert multiplier > 15  # mutmut default 15 timed out on a loaded runner
    # One mutant must stay well inside the job limit, suite measured up to 15 s.
    workflow = (Path(__file__).parents[1] / ".github/workflows/quality-report-selftest.yml").read_text()
    job = workflow.split("image-change-mutation:", 1)[1]
    job_minutes = int(re.search(r"timeout-minutes:\s*(\d+)", job)[1])
    assert (15 + constant) * multiplier <= job_minutes * 60 / 2
    verdict = SCRIPT.split("stats = json.loads")[1]
    assert re.search(r"any\([^)]*\"timeout\"", verdict, re.S)  # timeout stays a failure
