---
title: "Purge unused tags"
description: "The reference reaper: how marked tags get usage-gated and hard-deleted with knock purge, and how to wire your own usage oracle."
sidebar_position: 7
---

`knock purge` is the shipped reference implementation of the reaper role introduced by
[delegated tag deletion (ADR 0012)](https://github.com/trivoallan/knock/blob/main/docs/architecture/decisions/0012-delegated-tag-deletion.md).
It is isolated behind its own `UsageOraclePort` and is fully replaceable — if you already
have a reaper, `deletionMode: mark` still works; just don't run `knock purge`.

(For *why* knock marks instead of deleting, see
[the deletion & retention model](../explanation/deletion-and-retention.md).)

## The lifecycle of a purged tag

1. A tag falls out of its policy selection (e.g. a version is removed from the semver range).
   With `deletionMode: mark`, `knock reconcile` attaches a `pending-deletion` OCI referrer to
   the tag instead of hard-deleting it. The digest is unchanged and the tag stays pullable.

2. Run `knock purge` in **dry-run mode** (the default — no deletes happen):

   ```bash
   uv run knock purge
   # protect  myimage:old-tag  reason=prod_sighting  last_seen=2026-06-12T14:05:00Z
   ```

   While the tag's digest is still seen in production (within `KNOCK_PURGE_MIN_IDLE_DAYS`),
   purge reports it as `protect` — nothing is removed.

3. After `KNOCK_PURGE_MIN_IDLE_DAYS` pass with no production sighting, a subsequent dry run
   shows the tag as `purge`:

   ```bash
   uv run knock purge
   # purge  myimage:old-tag  idle_since=2026-06-06T14:05:00Z
   ```

4. Apply the purge (removes the tag and clears the `pending-deletion` mark):

   ```bash
   uv run knock purge --apply
   # purge  myimage:old-tag  [deleted]
   ```

## Fail-closed

If the usage oracle errors, times out, or returns an unparseable answer for a candidate,
`knock purge` treats that candidate as **still in use**: it protects the tag (never deletes it)
and continues to the next candidate. A flaky oracle can therefore only ever *spare* tags, never
trigger a mass purge of potentially live images. (If `KNOCK_USAGE_ORACLE_CMD` is not configured
at all, purge refuses to start — exit 3 — rather than run blind.)

## The oracle is replaceable

Set `KNOCK_USAGE_ORACLE_CMD` to any executable that speaks the contract: reads a JSON object
from stdin (`{"digest","image_ref","identity","since"}`) and writes
`{"last_seen": "<ISO timestamp or null>"}` to stdout. The reference implementation for Datadog
is at [`oracles/datadog.sh`](https://github.com/trivoallan/knock/blob/main/docs/examples/oracles/datadog.sh) — adapt the Datadog API call to your
setup (endpoint, metric/log query, environment tag).

## Required config

```bash
export KNOCK_PURGE_MIN_IDLE_DAYS=7          # idle window before a tag is eligible
export KNOCK_USAGE_ORACLE_CMD=docs/examples/oracles/datadog.sh
# plus DD_API_KEY, DD_APP_KEY, DD_SITE for the Datadog oracle
```

Both variables are required; missing either raises a `ConfigError` (exit code 3) before
touching the registry.
