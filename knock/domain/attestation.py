"""Build the in-toto Statement for knock's transform/hardening predicate (roadmap ①).

The annotation stamp (`stamp.py`) is the cheap index card; this is the heavy,
signable record it points at. Pure — no httpx / subprocess / os.environ, like
stamp.py. The predicate shape is a Pydantic model so its JSON Schema is derived,
never hand-written, and frozen as public API at /v1.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Frozen public API (spec Q1). A project-branded vanity URI (the convention SLSA
# itself follows with slsa.dev): it needn't resolve, stays stable across repo moves,
# and names no deploying org. Versioned at /v1.
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://knock.dev/predicate/transform/v1"


class TransformStepFact(BaseModel):
    """One resolved hardening step as recorded in the predicate (name + params)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class TransformPredicate(BaseModel):
    """knock's transform/hardening lineage — the novel fact BuildKit cannot know.

    Mirrors the `io.knock.*` annotation lineage, but signed and verifiable. `import`
    uses its public spelling (the same key as `io.knock.import`) via an alias.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    policy: str
    import_: str = Field(alias="import")
    variant: str
    transformed: bool  # True = rebuilt through a transform; False = copied/passed through as-is
    source: str  # source repo, e.g. "docker.io/library/redis" — never a destination
    source_digest: str  # the immutable digest the rebuild derived from
    builder_id: str
    created: str  # ISO-8601 build time
    transform_version: str  # content hash of the resolved transform (== io.knock.transform.version)
    steps: list[TransformStepFact] = Field(default_factory=list)


def _subject_digest(digest: str) -> dict[str, str]:
    """`sha256:abc` -> `{"sha256": "abc"}` (the in-toto subject digest shape)."""
    algo, sep, value = digest.partition(":")
    if not sep:
        return {"sha256": algo}  # no algo prefix -> assume sha256
    return {algo: value}


def build_transform_statement(
    *,
    subject_name: str,
    subject_digest: str,
    policy: str,
    import_name: str,
    variant: str,
    source: str,
    source_digest: str,
    builder_id: str,
    created: str,
    transform_version: str,
    steps: list[tuple[str, dict[str, Any]]],
    transformed: bool,
) -> dict[str, Any]:
    """Assemble the in-toto Statement whose subject is the rebuilt output digest.

    Pure: callers pass already-resolved facts (digests, the ISO timestamp, the
    resolved steps). Returns a plain dict so an adapter can serialize it to JSON.
    """
    predicate = TransformPredicate.model_validate(
        {
            "import": import_name,
            "policy": policy,
            "variant": variant,
            "transformed": transformed,
            "source": source,
            "source_digest": source_digest,
            "builder_id": builder_id,
            "created": created,
            "transform_version": transform_version,
            "steps": [TransformStepFact(name=n, params=p) for n, p in steps],
        }
    )
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": subject_name, "digest": _subject_digest(subject_digest)}],
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate.model_dump(by_alias=True),
    }


def transform_predicate_json_schema() -> dict[str, Any]:
    """Published JSON Schema for the transform predicate (frozen public API /v1).

    Derived from the Pydantic model — never hand-written (CLAUDE.md).
    """
    return TransformPredicate.model_json_schema(by_alias=True)


# The OCI referrer artifactType cosign v3 attaches an attestation under. The use case
# lists referrers of this type to decide whether a mirror digest is already signed
# (idempotent backfill). ponytail: a present cosign bundle ⇒ "knock already signed
# this digest"; sufficient for idempotence — we do not pull + verify each referrer.
# Upgrade path if another tool attaches attestations to the same digests: attach a
# knock-owned marker referrer instead and match that.
COSIGN_ATTESTATION_ARTIFACT_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"

# cosign v3 signing-config (schema v0.2). cosign v3 enables the signing-config by
# default and rejects the old --fulcio-url/--rekor-url/--tlog-upload flags, so
# Fulcio/Rekor must be expressed here. Pure: validFor.start is a constant epoch
# ("valid from the beginning") so no clock is needed.
SIGNING_CONFIG_MEDIA_TYPE = "application/vnd.dev.sigstore.signingconfig.v0.2+json"
_SIGNING_CONFIG_EPOCH = "1970-01-01T00:00:00Z"

# The public Sigstore instance — the CA and transparency log a blank keyless config
# resolves to. Only ever consulted for the `keyless` signer (see build_signing_config).
PUBLIC_FULCIO_URL = "https://fulcio.sigstore.dev"
PUBLIC_REKOR_URL = "https://rekor.sigstore.dev"


def _signing_service(url: str, operator: str) -> dict[str, Any]:
    return {
        "url": url,
        # majorApiVersion 1 is the only version cosign v3 signing-config v0.2 defines.
        "majorApiVersion": 1,
        "validFor": {"start": _SIGNING_CONFIG_EPOCH},
        "operator": operator,
    }


def build_signing_config(
    *, fulcio_url: str, rekor_url: str, operator: str, keyless: bool = False
) -> dict[str, Any]:
    """Build a cosign v3 signing-config (schema v0.2) as a plain dict.

    A non-empty fulcio/rekor URL adds the corresponding service entry (keyless CA /
    transparency log). What a *blank* URL means is signer-dependent, and deliberately
    so — do not collapse the two branches:

    - ``keyless=False`` (the ``kms`` / ``key`` signers): empty stays empty. Those
      signers already hold a key, need no CA, and an air-gapped deployment depends on
      ``cosign attest --key …`` contacting no service at all.
    - ``keyless=True``: there is no key to fall back on, so a config naming no CA is
      not air-gapped — cosign v3 simply refuses it ("no fulcio URLs provided in
      signing config"). Blank therefore resolves to the public Sigstore instance,
      which is what "keyless with no further configuration" has always meant. Rekor
      is defaulted alongside Fulcio because Sigstore keyless signing is
      transparency-log-backed: a CA with no log is a half-configuration.
    """
    if keyless:
        fulcio_url = fulcio_url or PUBLIC_FULCIO_URL
        rekor_url = rekor_url or PUBLIC_REKOR_URL
    config: dict[str, Any] = {
        "mediaType": SIGNING_CONFIG_MEDIA_TYPE,
        "rekorTlogConfig": {},
        "tsaConfig": {},
    }
    if fulcio_url:
        config["caUrls"] = [_signing_service(fulcio_url, operator)]
    if rekor_url:
        config["rekorTlogUrls"] = [_signing_service(rekor_url, operator)]
        config["rekorTlogConfig"] = {"selector": "ANY"}
    return config
