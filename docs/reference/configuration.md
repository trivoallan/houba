---
sidebar_position: 2
---

# Configuration

Each field is set as `KNOCK_<FIELD>` (the property name upper-cased). JSON-typed fields (`registries`, `transform_ca_certs`, `transform_package_mirrors`, `retention`) take a JSON value whose shape is documented in the [schemas](schemas/) section. The machine-readable contract is [`config.schema.json`](config.schema.json).

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `KNOCK_LABEL_PREFIX` | string | `io.knock` | Prefix for knock's own provenance annotations; empty ⇒ no knock labels (OCI-standard keys only). |
| `KNOCK_REGISTRIES` | JSON object | `{}` | JSON map of logical registry name → `RegistryConfig`. At least one is needed to reconcile. |
| `KNOCK_LOG_FORMAT` | string | `text` | Log output format: `text` or `json`. |
| `KNOCK_LOG_LEVEL` | string | `INFO` | Minimum log level. |
| `KNOCK_DRY_RUN_TAGS` | boolean | `false` | Skip image copies / pushes. |
| `KNOCK_DRY_RUN_DELETIONS` | boolean | `false` | Skip deletions. |
| `KNOCK_DELETION_MODE` | string | `purge` | Global baseline of the deletion-mode cascade. |
| `KNOCK_RETENTION` | JSON object | `(unset)` | Global tier of the retention cascade (a JSON `Archive`); unset ⇒ retention off everywhere. |
| `KNOCK_WORK_DIR` | string | `/tmp/knock-work` | Scratch directory for build contexts. |
| `KNOCK_TRANSFORM_CA_CERTS` | JSON object | `{}` | JSON map of name → CA source, resolved by the `injectCA` transform. |
| `KNOCK_TRANSFORM_PACKAGE_MIRRORS` | JSON object | `{}` | JSON map of name → package mirror, resolved by `rewritePackageSources`. |
| `KNOCK_BUILD_PLATFORM` | string | `linux/amd64` | Platform for the rebuild path (single-platform). |
| `KNOCK_SBOM_FORMATS` | JSON list | `["spdx-json"]` | SBOM formats syft emits on every placed image (copy and rebuild), as a JSON list. Allowed: spdx-json, cyclonedx-json. Non-empty — the knob chooses which formats, never whether (always-on coverage). |
| `KNOCK_MAX_CONCURRENCY` | integer | `4` | Max parallel tag operations per run (`1` = sequential). |
| `KNOCK_ATTEST_SIGNER` | string | `(empty)` | Signing mode for SLSA attestations on the rebuild path; empty ⇒ off. |
| `KNOCK_ATTEST_KEY_REF` | string | `(empty)` | KMS URI (`kms`) or key path (`key`). |
| `KNOCK_ATTEST_FULCIO_URL` | string | `(empty)` | Keyless CA URL; blank ⇒ public Fulcio for `keyless`, no CA for `kms`/`key`. |
| `KNOCK_ATTEST_REKOR_URL` | string | `(empty)` | Transparency-log URL; blank ⇒ public Rekor for `keyless`, no log for `kms`/`key`. |
| `KNOCK_ATTEST_BUILDER_ID` | string | `(empty)` | URI identifying this knock builder. |
| `KNOCK_ATTEST_VERIFY_IDENTITY` | string | `(empty)` | Keyless verify identity regexp (KNOCK_ATTEST_VERIFY_IDENTITY). |
| `KNOCK_ATTEST_VERIFY_OIDC_ISSUER` | string | `(empty)` | Keyless verify OIDC issuer (KNOCK_ATTEST_VERIFY_OIDC_ISSUER). |
| `KNOCK_USAGE_ORACLE_CMD` | string | `(unset)` | Executable speaking the usage-oracle contract; required to run `knock purge`. |
| `KNOCK_USAGE_ORACLE_TIMEOUT` | integer | `30` | Per-query timeout (seconds) for the usage oracle. |
| `KNOCK_PURGE_MIN_IDLE_DAYS` | integer | `(unset)` | Idle window `knock purge` requires before reaping a marked tag. |
