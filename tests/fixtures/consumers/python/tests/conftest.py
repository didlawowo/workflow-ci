from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from consumer_fixture import add  # noqa: E402


assert add(1, 1) == 2
