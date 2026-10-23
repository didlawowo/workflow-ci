"""Capture bounded engine evidence before leaving the isolated execution job."""

import argparse
import base64
import gzip
import io
import json
from pathlib import Path

from mutation_evidence import pack, project


def capture(root: Path, base: str, head: str) -> dict:
    root = root.resolve(strict=True)
    for relative in (
        "mutants/mutmut-cicd-stats.json",
        ".quality/mutation-no-targets.json",
        ".quality/gremlins.json",
        ".quality/stryker.json",
    ):
        path = root / relative
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError("Mutation report escapes the checkout")
            quality = (
                project(root, ".quality")
                if (root / ".quality").exists()
                else root / ".quality"
            )
            return pack(
                path,
                quality / "mutmut-results.txt",
                quality / "mutation-v2-review.json",
                base,
                head,
            )
    raise ValueError("Mandatory mutation run produced no supported mutation evidence")


def decode(source: Path, output: Path) -> None:
    if source.stat().st_size > 1_000_000:
        raise ValueError("Oversized encoded mutation evidence")
    envelope = json.loads(source.read_text())
    if (
        not isinstance(envelope, dict)
        or envelope.get("evidence-file") != "mutation-evidence.json"
    ):
        raise ValueError("Invalid mutation evidence filename")
    encoded = envelope.get("evidence-b64")
    if not isinstance(encoded, str) or len(encoded) > 700_000:
        raise ValueError("Missing or oversized encoded mutation evidence")
    compressed = base64.b64decode(encoded, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as handle:
        raw = handle.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ValueError("Oversized decompressed mutation evidence")
    json.loads(raw)
    output.write_bytes(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", nargs="?", default="capture", choices=("capture", "decode")
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "decode":
        if not args.input:
            parser.error("decode requires --input")
        decode(args.input, args.output)
    else:
        if not args.root or not args.base or not args.head:
            parser.error("capture requires root/base/head")
        raw = (
            json.dumps(capture(args.root, args.base, args.head), sort_keys=True) + "\n"
        )
        if len(raw.encode()) > 20_000_000:
            raise ValueError("Oversized captured mutation evidence")
        args.output.write_text(raw)
