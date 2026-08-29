---
title: "GC superseded scan referrers"
description: "Reap stale scan referrers with knock gc, keeping the newest per (tool, format)."
sidebar_position: 8
---

`knock attach` writes one scan-result referrer per scan. Over time, repeat scans of the same image
pile up superseded referrers. `knock gc` walks the registry roster, keeps the newest per
`(tool, format)` group on each subject, and collects the rest.

## Plan first (default: dry-run, nothing deleted)

```bash
uv run knock gc --keep 2 --older-than-days 30
# COLLECT  localhost:5001/demo/redis:7.2.0  kept=2 collected=3 (planned)
# [dry-run] collected=3 error=0
```

## Bound the walk to one registry

```bash
uv run knock gc --registry local
```

`--registry NAME` selects a single entry from `KNOCK_REGISTRIES`, exactly like `audit` / `purge`.

## Apply (actually delete the superseded referrers)

```bash
uv run knock gc --keep 2 --older-than-days 30 --apply
# COLLECT  localhost:5001/demo/redis:7.2.0  kept=2 collected=3 [applied]
# [apply] collected=3 error=0
```

`KNOCK_DRY_RUN_DELETIONS=1` is the deployment-wide kill-switch: it forces dry-run even with
`--apply` (shared with `reconcile` / `purge`).

## The retention rule

Within each `(tool, format)` group on a subject, the `--keep` newest referrers are always retained;
among the rest, only those older than `--older-than-days` are collected (both conditions must hold).
A Trivy vulnerability scan and a `regis` posture report on the same image never reap each other —
different tools are independent groups. A referrer whose scan timestamp is missing or unparseable is
ignored (never collected): knock only deletes what it understands.

The paired signed scan attestation is **not** reaped in v1 (correlating it requires parsing the
signed predicate); a collected report can leave an orphan attestation, tracked as a follow-up.
