from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_GO = ROOT / ".github" / "actions" / "setup-go-env" / "action.yml"


def test_go_cache_keeps_module_downloads_without_archiving_build_objects():
    content = SETUP_GO.read_text()

    assert "uses: actions/setup-go@v6" in content
    assert "cache: false" in content
    assert "uses: actions/cache@v5" in content
    assert "download=$(go env GOMODCACHE)/cache/download" in content
    assert "path: ${{ steps.cache-path.outputs.download }}" in content
    assert (
        "${{ runner.os }}-${{ runner.arch }}-${{ steps.setup.outputs.go-version }}"
        in content
    )
    assert "${{ hashFiles(inputs.cache-dependency-path) }}" in content
    assert "path: ${{ env.GOCACHE }}" not in content
    assert "path: ${{ env.GOMODCACHE }}" not in content


def test_go_cache_hit_output_comes_from_the_module_download_cache():
    content = SETUP_GO.read_text()

    assert "value: ${{ steps.module-cache.outputs.cache-hit }}" in content
    assert "value: ${{ steps.setup.outputs.cache-hit }}" not in content
