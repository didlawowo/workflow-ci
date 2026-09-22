# Consumer acceptance fixes: issues 79 and 80

Baseline: `896a421f6d4d49800008c61bd58c0e23f476b325` (main).
The PR range expression from #78 and the runner routing from #81 were already
merged. This patch preserves both and completes the remaining acceptance gaps.

## Mutation report (#79)

The publisher forwards `needs.mutation.result` independently of `required`.
Only successful trusted classification with explicit `false` makes mutation
optional. Failed/cancelled execution is FAIL; missing/skipped/unknown execution,
missing classification, or required-but-missing evidence is INCOMPLETE, never PASS.
The sticky comment replaces stale mutation evidence with the latest authoritative
result, including failures without an artifact. Standalone legacy report calls
remain compatible, but cannot overwrite an authoritative mutation result.
A failed artifact download still permits a diagnostic report; a final publication
check keeps that failure blocking. Permissions and runner routing are unchanged.

## Secret scan (#80)

The trusted wrapper reads PR base/head from the event JSON (not the merge SHA or
synchronize `before`). Existing-branch pushes use before/after. New branches,
manual/scheduled runs, equal refs and rewritten histories receive a full reachable
history scan. Missing PR commits and shallow history fail closed.

The official TruffleHog Docker image and verified-only policy are preserved.
As with the previous pinned action, the underlying image uses its `latest` default.
The wrapper adds `--fail-on-scan-errors`, validates JSON, and distinguishes exit 183
with real findings from scanner/infrastructure errors. Raw credential-bearing
JSON/stderr are never published and temporary files are deleted. The quality
report shows actual findings separately from scanner execution errors.

Go and Node actions do not invoke TruffleHog and do not contain the faulty range.
This patch does not add new scanners to those languages or change their policy.

## Release / acceptance

No existing tag is moved and no consumer is switched to main or a commit SHA.
Publish the next maintainer-controlled patch tag after review and green CI, then
rerun JetRacer #44/#45 on that tag. Retrying the unchanged v1.8.0 consumer does not
test this patch. The historical pre-step job annotations were not accessible
through the connector; the original scheduling cause is not asserted here.
