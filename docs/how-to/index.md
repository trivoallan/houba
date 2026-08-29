---
sidebar_label: "How-to guides"
sidebar_position: 0
---

# How-to guides

Task-oriented guides — each solves one concrete problem against a running knock.

- [Verify a published stamp](verify-published-stamp.md) — read the stamp, fetch the SBOM and verify the signature on a live public image, with stock tools and no account.
- [Attach a scan result](attach-scan.md) — ingest an upstream SARIF report as a signed OCI referrer; use `--fail-on` as a severity CI gate.
- [GC superseded scan referrers](gc-scan-referrers.md) — reap stale scan referrers with `knock gc`, keeping the newest per (tool, format).
- [Purge unused tags](purge-unused-tags.md) — usage-gate and hard-delete marked tags with `knock purge`; wire your own usage oracle.
- [Audit coverage](audit-coverage.md) — find images that lack the stamp with `knock audit`; gate CI on coverage, signing, or SBOM presence.
- [Inspect an image's SBOM](inspect-sbom.md) — find and fetch the SBOM referrer attached to a placed image; enable CycloneDX alongside SPDX.
- [Run the reference deployment](reference-deployment.md) — run knock as a Kubernetes CronJob: git-synced policies, rootless buildkitd, a blast-radius consumer, and optional KEDA autoscaling.
- [Rebuild and harden an image](rebuild-and-harden.md) — derive-and-stamp on the rebuild path: inject internal CAs, rewrite package sources to internal mirrors, and sign the result.
- [Migrate off registry replication](migrate-from-replication.md) — replace a legacy CI + replication intake with knock destinations; OCI referrers survive placement where replication strips them.
