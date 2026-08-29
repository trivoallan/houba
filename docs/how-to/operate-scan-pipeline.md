---
title: "Operate the scan pipeline"
description: "Run, monitor, and triage the incremental Redis-Streams scan pipeline: first green scan, daily signals, and runbooks for a stuck queue, a growing dead-letter stream, and a coverage gap."
sidebar_position: 12
---

The scan pipeline is **incremental and reconcile-fed**: `knock reconcile` places a digest into the
destination registry, then the **enqueuer** `XADD`s it to the Redis Stream `knock:scan:work` and
records it in the `knock:scan:placed` SET; a **KEDA-scaled scan-worker** Job picks it up with
`XREADGROUP`, runs the scanner, calls `knock attach` to sign and publish the verdict as an OCI
referrer, and sends an `XACK`; a **coverage CronJob** wakes on a schedule and diffs
`knock:scan:placed` against `knock:scan:confirmed` to produce the coverage report. The operator's
job is to keep the work stream draining, watch the dead-letter stream stay empty, and triage what
falls through.

## First green scan

This is your "hello world" for the pipeline. The component ships in `deploy/overlays/local` — the
same overlay you ran for the reference deployment.

**1. Trigger a reconcile.**

```bash
kubectl create job --from=cronjob/knock-reconcile first-reconcile -n knock
```

**2. Watch a scan-worker Job reach `Complete`.**

```bash
kubectl get jobs -n knock -w
# NAME                    COMPLETIONS   DURATION   AGE
# first-reconcile         1/1           8s         12s
# scan-worker-<hash>      1/1           42s        20s   ← the scan job
```

**3. Confirm the digest landed in the confirmed set.**

```bash
kubectl exec deploy/scan-queue-redis -n knock -- \
  redis-cli ZRANGE knock:scan:confirmed 0 -1
# "sha256:abc123…"
```

The signal you have succeeded: a non-empty `knock:scan:confirmed` and a `SCAN` column in
`make blast-radius` output. The elapsed time from `kubectl create job` to the first entry in
`knock:scan:confirmed` is your **pipeline latency baseline** — record it, since the KEDA idle
threshold below is a multiple of it.

:::tip No entries in confirmed?
Run `kubectl logs -n knock -l job-name=scan-worker-<hash>` to see whether `knock attach` exited 0.
The most common causes at first run are a missing `KNOCK_ATTEST_SIGNER` (attach needs the signer
env var to sign) and a registry auth mismatch.
:::

## Daily signals

Check these every morning, or wire them into your alerting stack. All Redis commands run against
the `scan-queue-redis` Deployment in the `knock` namespace.

| Signal | Command | What it means |
|--------|---------|---------------|
| Work backlog | `redis-cli XLEN knock:scan:work` | Messages waiting to be claimed — should drain between reconcile ticks |
| In-flight (PEL) | `redis-cli XPENDING knock:scan:work scan - + 10` | Messages claimed but not yet acked — persistent entries here point to stuck workers |
| Dead-letter size | `redis-cli XLEN knock:scan:dead` | Messages that exhausted retries — should stay at 0 |
| Coverage gap | JSON output from the `scan-coverage` CronJob, field `coverage_gap` | Placed images that have no confirmed fresh scan |
| Coverage by owner | `by_owner` map in the same JSON | Which team owns uncovered images |
| Oldest pending age | oldest entry's idle ms from `XPENDING` | Should stay below `REAP_MIN_IDLE_MS`; a breach means a worker is stuck or crashed |

**Alerts to configure** (not shipped by knock — add them in your monitoring stack):

- `XLEN knock:scan:dead` rising — dead-letter entries are actionable; they never self-resolve.
- `coverage_gap` above your threshold (e.g. > 5 % of placed images).
- `enqueue_failed` counter > 0 — the reconcile→enqueuer handoff failed; images are placed but will
  never be scanned unless re-enqueued.
- Oldest-pending age > `REAP_MIN_IDLE_MS` — a PEL entry that ages past the reap window will be
  double-claimed (see [Sizing note](#sizing-note) below).

## Runbook — queue not draining

Use this when the work backlog (`XLEN knock:scan:work`) is not falling over several reconcile
cycles, or the oldest-pending age in the PEL is rising.

**1. Is Redis reachable?**

```bash
kubectl exec deploy/scan-queue-redis -n knock -- redis-cli PING
# PONG  ← healthy
```

No PONG: restart the pod — AOF persistence restores the stream on startup.

```bash
kubectl rollout restart deployment/scan-queue-redis -n knock
kubectl rollout status deployment/scan-queue-redis -n knock
```

**2. Are scan-worker Jobs being created?**

```bash
kubectl get jobs -n knock -l app=scan-worker
```

No recent Jobs: check the KEDA `ScaledJob` targeting `knock:scan:work`:

```bash
kubectl describe scaledjob scan-worker -n knock
kubectl describe scaledobject scan-worker-scaler -n knock   # if ScaledObject is used
```

Common causes: KEDA cannot reach the Redis metrics endpoint (check the KEDA operator logs), or the
scan-worker image pull is failing (check `kubectl describe job <name> -n knock` for
`ImagePullBackOff`).

**3. Are Jobs being created but not completing?**

```bash
kubectl get jobs -n knock -l app=scan-worker
kubectl logs -n knock -l app=scan-worker --tail=50
```

If every Job fails with a scanner error (e.g. `grype` cannot reach its CVE database, or the
destination registry is rate-limiting), the backlog will stay full until scanner capacity
recovers — the stream will drain on its own once the scanner is healthy.

**4. Is one digest looping?**

A single malformed image or a digest the registry has deleted can loop indefinitely if XAUTOCLAIM
keeps re-delivering it before it hits the dead-letter threshold. Identify it:

```bash
kubectl exec deploy/scan-queue-redis -n knock -- \
  redis-cli XPENDING knock:scan:work scan - + 10
# shows message ids with delivery count
```

Inspect the stuck digest:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py show <digest>
```

If the image is permanently gone, drop it:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py drop <digest>
```

If the failure was transient (network blip, rate limit), replay it:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py replay <digest>
```

**Verify the queue is draining.**

```bash
kubectl exec deploy/scan-queue-redis -n knock -- redis-cli XLEN knock:scan:work
# should be falling on successive checks
kubectl exec deploy/scan-queue-redis -n knock -- \
  redis-cli XPENDING knock:scan:work scan - + 1
# idle ms on the oldest entry should be falling
```

## Runbook — dead stream growing

Run this when `XLEN knock:scan:dead` is above 0 and rising.

**1. List what is in the dead stream.**

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py list
# DIGEST                              REASON                  SUGGESTED_ACTION
# sha256:abc123…                     registry 404            drop (image gone)
# sha256:def456…                     network timeout         replay
```

Read each `suggested_action`. After a transient outage (registry down, DNS flap), replay all:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py replay --all
```

For permanently-gone images (registry 404, digest deleted), drop them:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py drop <digest>
```

**Verify the dead stream is shrinking.**

```bash
kubectl exec deploy/scan-queue-redis -n knock -- redis-cli XLEN knock:scan:dead
# should be falling after replays and drops
```

:::note Raw escape hatch
If `scan-dlq.py` is unavailable (e.g. the knock pod is crash-looping), read the dead stream
directly with `redis-cli`:
```bash
kubectl exec deploy/scan-queue-redis -n knock -- \
  redis-cli XRANGE knock:scan:dead - + COUNT 20
```
:::

## Runbook — coverage gap above threshold

Run this when the `scan-coverage` CronJob's output shows `coverage_gap` above your SLA, or when
the coverage metric alert fires.

**1. Read the coverage output.**

```bash
kubectl logs -n knock -l app=scan-coverage --tail=100
# {
#   "coverage_gap": 3,
#   "placed": 42,
#   "confirmed": 39,
#   "by_owner": {
#     "team-a": ["sha256:abc123…", "sha256:def456…"],
#     "team-b": ["sha256:789fed…"]
#   }
# }
```

The `by_owner` map tells you which team owns each uncovered image. Page the relevant owner with
the list of digests — they need to either ensure the image passes through the front door or
acknowledge the gap.

**2. Check whether the images are actually in the work stream.**

If a digest appears in `placed` but is not in `work` and not in `confirmed`, the enqueuer may have
missed it. Re-enqueue manually:

```bash
kubectl exec deploy/scan-queue-redis -n knock -- \
  redis-cli XADD knock:scan:work '*' digest sha256:abc123…
```

**Verify the gap is shrinking.**

Wait for the next coverage CronJob run (or trigger it manually), then check again:

```bash
kubectl create job --from=cronjob/scan-coverage coverage-check -n knock
kubectl logs -n knock -l job-name=coverage-check --tail=100
# coverage_gap should be lower
```

:::warning Gap semantics
The coverage report **over-reports rather than under-reports**: a digest counts as uncovered until
a fresh signed scan referrer lands on it. A non-zero gap is always a real to-do — it never masks
a missed image. Act on every non-zero gap; do not wait to see whether it resolves on its own.
:::

## `scan-dlq` reference

Run `scan-dlq.py` inside a knock pod:

```bash
kubectl exec -it <knock-pod> -n knock -- \
  python3 /scripts/scan-dlq.py <subcommand>
```

| Subcommand | What it does |
|------------|-------------|
| `list` | Print a table of all entries in `knock:scan:dead`: digest, failure reason, delivery count, suggested action |
| `show <digest>` | Print full context for one dead entry: the original message fields, failure history, and a specific suggested fix |
| `replay <digest>` | Re-enqueue a single digest back to `knock:scan:work` and remove it from the dead stream |
| `replay --all` | Re-enqueue every entry in the dead stream; use after a transient outage clears |
| `drop <digest>` | Permanently remove a digest from the dead stream with no re-enqueue; use when the image is gone |

For raw inspection without the script, `redis-cli XRANGE knock:scan:dead - + COUNT 20` is the
escape hatch.

## Sizing note

:::warning Set REAP_MIN_IDLE_MS carefully
Set `REAP_MIN_IDLE_MS` to at least **2 × the p99.9 scan duration**, measured on your largest real
image. XAUTOCLAIM reclaims a message purely on idle time — a long-but-alive scan that exceeds the
window will be claimed by a second worker while the first is still running, creating a
double-execution. Alert if any individual scan exceeds `REAP_MIN_IDLE_MS / 2` so you can tune
the threshold before it causes a double-claim.
:::
