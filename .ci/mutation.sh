#!/usr/bin/env bash
set -euo pipefail

python -m pip install -q pytest mutmut
rm -rf mutants
mutmut run
mutmut export-cicd-stats
test -f mutants/mutmut-cicd-stats.json
