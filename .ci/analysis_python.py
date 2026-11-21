"""Python version used by trusted code that parses the sources of a project.

Trusted verifiers parse consumer sources with `ast`, and an interpreter older
than the project cannot read its syntax. They follow the same convention as the
isolated mutation environment: the `.python-version` of the project, limited to
the supported minors. The file content is only compared, never executed.
"""

from __future__ import annotations

from pathlib import Path
import sys

DEFAULT = "3.12"
SUPPORTED = ("3.12", "3.13", "3.14")


def analysis_python(project: Path) -> str:
    pin = project / ".python-version"
    if pin.is_symlink():
        raise ValueError("Consumer .python-version must be a regular file")
    if not pin.is_file():
        return DEFAULT
    lines = pin.read_text(encoding="utf-8", errors="replace").splitlines()
    requested = lines[0] if lines else ""
    if requested not in SUPPORTED:
        raise ValueError(
            "Unsupported consumer .python-version; expected "
            + ", ".join(SUPPORTED[:-1])
            + f" or {SUPPORTED[-1]}"
        )
    return requested


def running_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: analysis_python.py <project directory>")
    try:
        print(analysis_python(Path(sys.argv[1])))
    except ValueError as error:
        sys.exit(f"::error::{error}")
