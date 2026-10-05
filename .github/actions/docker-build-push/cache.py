"""Export registry caches sequentially after the image, one tag per platform."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def configuration(env):
    image, scope = env["CACHE_IMAGE"], env["CACHE_SCOPE"]
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*", image)
        or ":" in image.rsplit("/", 1)[-1]
    ):
        raise ValueError("CACHE_IMAGE must be a repository without a tag")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", scope):
        raise ValueError("Invalid cache scope")
    platforms = [p.strip() for p in env["CACHE_PLATFORMS"].split(",")]
    if not platforms or any(
        not re.fullmatch(r"[a-z0-9]+/[a-z0-9]+(?:/[a-z0-9_.-]+)?", p) for p in platforms
    ):
        raise ValueError("Invalid cache platforms")
    tags = []
    for platform in dict.fromkeys(platforms):
        tag = "buildcache-" + scope + "-" + platform.replace("/", "-")
        if len(tag) > 128:
            raise ValueError("Cache tag exceeds registry limit")
        tags.append((platform, image + ":" + tag))
    return tags, image + ":buildcache-" + scope


def imports(env):
    tags, legacy = configuration(env)
    return ["type=registry,ref=" + ref for _, ref in tags] + [
        "type=registry,ref=" + legacy
    ]


def command(env, platform, ref):
    cmd = [
        "docker",
        "buildx",
        "build",
        "--output",
        "type=cacheonly",
        "--platform",
        platform,
    ]
    if env.get("CACHE_NATIVE") == "true":
        cmd.extend(["--builder", "native"])
    cmd.extend(["--file", env["CACHE_DOCKERFILE"]])
    if env.get("CACHE_TARGET"):
        cmd.extend(["--target", env["CACHE_TARGET"]])
    for name, flag in (
        ("CACHE_BUILD_ARGS", "--build-arg"),
        ("CACHE_LABELS", "--label"),
    ):
        for line in env.get(name, "").splitlines():
            if line.strip():
                cmd.extend([flag, line])
    for source in imports(env):
        cmd.extend(["--cache-from", source])
    cmd.extend(
        [
            "--cache-to",
            "type=registry,mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=false,ref="
            + ref,
        ]
    )
    cmd.extend(["--", env["CACHE_CONTEXT"]])
    return cmd


def inspect_cache(env, ref, run):
    # Docker reuses login credentials and handles Registry Basic/Bearer challenges.
    cmd = ["docker", "buildx", "imagetools", "inspect", "--raw", ref]
    if env.get("CACHE_PLAIN_HTTP") == "true":
        cmd = ["docker", "manifest", "inspect", "--insecure", ref]
    result = run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return {}


def export(env, run=subprocess.run):
    required = env.get("CACHE_REQUIRED", "false")
    if required not in ("true", "false"):
        raise ValueError("CACHE_REQUIRED must be true or false")
    tags, _ = configuration(env)
    failed = []
    for platform, ref in tags:
        print("Exporting cache for " + platform + " to " + ref, flush=True)
        for attempt in range(2):
            try:
                result = run(command(env, platform, ref), check=False)
                manifest = (
                    inspect_cache(env, ref, run) if result.returncode == 0 else {}
                )
            except OSError:
                manifest = {}
            if isinstance(manifest, dict):
                config = manifest.get("config")
                if (
                    isinstance(config, dict)
                    and config.get("mediaType")
                    == "application/vnd.buildkit.cacheconfig.v0"
                ):
                    print("Verified registry cache: " + ref, flush=True)
                    break
            if attempt == 0:
                print("::warning::Retrying cache export for " + platform, flush=True)
        else:
            failed.append(platform)
            print(
                "::warning::Registry cache unavailable after export: " + ref, flush=True
            )
    if failed and required == "true":
        raise RuntimeError("Required cache exports failed: " + ", ".join(failed))
    return not failed


if __name__ == "__main__":
    env = os.environ
    output = Path(env["GITHUB_OUTPUT"])
    if sys.argv[1] == "plan":
        value = "\n".join(imports(env))
        with output.open("a") as file:
            file.write("cache-from<<CACHE_IMPORTS\n" + value + "\nCACHE_IMPORTS\n")
    elif sys.argv[1] == "export":
        success = export(env)
        with output.open("a") as file:
            file.write("exported=" + str(success).lower() + "\n")
    else:
        raise ValueError("Expected plan or export")
