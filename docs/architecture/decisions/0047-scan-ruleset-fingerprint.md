# 47. A signed scan verdict records the ruleset it was reached under

Date: 2026-08-29

## Status

Accepted

## Context

`knock attach` signs an in-toto statement asserting a normalized summary of an upstream scan against
an image digest. The statement identifies the producer as `scanner {name, version}` — and nothing
else about *how* the verdict was reached.

For a vulnerability scanner that is close enough: the tool version implies the detection logic. For
a **policy-as-code** analyzer it is not. The sibling tool regis evaluates a *playbook* — rules and
thresholds resolved at run time — so one version of the binary returns different verdicts under
different rulesets. A signed "this digest passed", with the ruleset unnamed, is a statement whose
meaning changes when someone loosens a threshold. That is the failure ADR
[0020](0020-revision-semantics.md) refused for `org.opencontainers.image.revision`: never assert
more than is known.

regis already computes the fact — a sha256 fingerprint of the resolved, enforced ruleset, published
as `ruleset_hash` — but knock discarded it on ingestion. That also forfeited a cheap query: *which
placed images were judged under an obsolete ruleset?* is a set difference over an annotation if the
value is in the fact space, and a fetch-and-parse of every raw SARIF referrer if it is not.

## Decision

The SARIF ingestion profile gains an optional `ruleset_hash`, surfaced as the `ruleset.hash` fact
(annotation `{prefix}.scan.ruleset.hash`, and the same key in the signed `scan/v1` summary).

- **Canonical location is `runs[].properties`.** A run-level property bag exists on every run,
  breached or clean. knock falls back to the first `runs[].results[].properties.ruleset_hash` so a
  producer that hangs it off a `kind: "pass"` receipt is still read; run level wins on conflict.
- **Opaque and verbatim.** knock does not parse, normalize, or validate the value; it is an equality
  token for consumers, not a structure knock interprets.
- **Omitted when absent.** No fallback to the tool version, the report digest, or a hash of the rule
  list. A consumer reads an absent key as *unknown*, never as *unchanged* — ADR 0020's rule.
- **Not a gate.** `--fail-on` and `knock verify --require scan-pass` are untouched. Deciding that a
  fingerprint is stale requires knowing the current ruleset, which is the operator's fact, not the
  image's; knock records, the org's query decides.

No model change: `ScanSummary.facts` and `ScanPredicate.summary` are already open `dict[str, str]`,
so the published predicate schema is unchanged. Not a count, so it is not in the mapper's
`fact_keys`; it joins `scan.tool.version` in the vocabulary's optional keys. No new port, adapter,
actor, or external system. **C4 model: unchanged.**

## Consequences

- A signed scan verdict is self-describing: scanner, version, **and** the rules applied.
- "Judged under an obsolete ruleset" becomes an annotation set-difference across the fleet, with no
  referrer blob fetched or SARIF parsed.
- Value depends on producers emitting it. regis today attaches `ruleset_hash` only to the clean-run
  pass receipt, so a **breached** run carries no fingerprint — the case where it is most wanted.
  knock reads the receipt anyway, so the fact lands on clean runs immediately; the profile documents
  run-level as canonical, and moving regis to it is a parallel sibling-repo change.
- Coverage is partial and legitimately so: a mixed fleet holds images with the fact and images
  without, and the two are not comparable. Hence *unknown*, not *unchanged*.
