"""Python version used by trusted code that parses the sources of a project.

Trusted verifiers parse consumer sources with `ast`, and an interpreter older
than the project cannot read its syntax. They follow the same convention as the
isolated mutation environment: the `.python-version` of the project, limited to
the supported minors, read the same way (first line, taken literally). The file
content is only compared, never executed. MUTATION_PYTHON_VERSION, an optional
runner default of the mutation environment, is not read here.
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
    requested = pin.read_bytes().split(b"\n", 1)[0].decode("utf-8", errors="replace")
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
    if len(sys.argv) != 3:
        sys.exit("usage: analysis_python.py <checkout> <project directory>")
    # Same directory validation as the verifiers, before anything is read.
    from mutation_evidence import project

    try:
        print(analysis_python(project(Path(sys.argv[1]), sys.argv[2])))
    except (OSError, ValueError) as error:
        sys.exit(f"::error::{error}")
