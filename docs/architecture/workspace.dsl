workspace "knock" "Single front door / stamper for external container images." {

    # knock is the subject of the Context / Container / Component / Deployment views — always
    # drawn as the boundary, never as a plain element — so the "element not on any view"
    # inspection false-positives on it (it does render as a box in the Landscape view).
    # Downgrade just that one inspection; every other inspection stays a hard CI gate.
    properties {
        "structurizr.inspection.model.element.noview" "ignore"
    }

    model {
        platformEng = person "Platform / Security Engineer" "Owns the hardening policy and the registry roster; operates knock as the single front door for external images."
        productTeam = person "Product / Application Team" "Declares its imports as MirrorPolicy files and consumes the hardened, stamped images."
        incidentResponder = person "Incident Responder (SRE / Security)" "At CVE time, computes the blast-radius from knock's provenance stamp."

        knock = softwareSystem "knock" "Single front door / stamper: mirrors external container images, optionally rebuilds them through a hardening policy, and stamps them with standardized, portable provenance." "Target" {
            # Documentation + decisions attached to knock (rendered in the viewer's panes):
            #   !docs → the design overview (docs/architecture/design.md).
            #   !adrs → the design specs as ADRs (docs/architecture/decisions/), linking out
            #           to the full specs under docs/superpowers/specs/.
            !docs design.md
            !adrs decisions

            knockCli = container "knock CLI" "Reconcile engine: loads MirrorPolicy files, mirrors or rebuilds images and stamps them with provenance. Runs as a CLI / Job; the runtime image bundles regctl + buildctl." "Python · Typer" {

                group "CLI" {
                    cliMain = component "main" "Typer entrypoint; maps exceptions to exit codes." "Typer"
                    cliReconcile = component "reconcile" "The reconcile command: builds the composition root, runs the loop, renders the report." "Typer"
                    cliPurge = component "purge" "The purge command: scans pending-deletion marks, queries the usage oracle, applies hard-deletes for safely-unused tags." "Typer"
                    cliAudit = component "audit" "The audit command: catalog-walks the registry, reports images missing the provenance stamp." "Typer"
                    cliAttach = component "attach" "The attach command: ingests an upstream scan report and attaches it as a stamped OCI referrer." "Typer"
                    cliGc = component "gc" "The gc command: catalog-walks the registry and deletes superseded scan-result referrers, keeping the N newest per (tool, format) older than a grace window." "Typer"
                    cliVerify = component "verify" "The verify command: reads knock's facts for a digest (signed scan attestation, stamp, SBOM referrer) and produces a single exit-0/1 gate verdict." "Typer"
                    cliScan = component "scan" "The scan command group (knock-oci[scan] optional extra): reserve, attach, enqueue, reaper sub-commands for the incremental scan pipeline." "Typer"
                    cliRender = component "render" "Formats the RunReport to stdout (text / JSON)." "Python"
                    cliDi = component "_di" "Composition root: wires ports to adapters." "Python"
                }
                group "Use cases" {
                    ucLoader = component "loader" "Loads and parses every MirrorPolicy file in a directory." "Python"
                    ucReconcile = component "reconcile_policies" "Driver: enforces the ownership invariant, shards, partitions policies by source class, runs one collision check across every planner's aliases, then plan-then-apply. Isolated per policy, shardable for scale-out." "Python"
                    ucPolicyPlanner = component "policy_planner" "The PolicyPlanner protocol (handles / plan / apply): one implementation per source class. An internal orchestration seam between use cases, deliberately NOT a port — ports/ is reserved for I/O boundaries an adapter implements." "typing.Protocol"
                    ucReconcileRegistry = component "reconcile_registry" "The registry-sourced planner: tag selection, variant expansion, copy or hardening rebuild, stamp, retention and deletion." "Python"
                    ucReconcileGit = component "reconcile_git" "The git-sourced planner: resolve the ref to a revision, compare against the destination's sha-<revision> tags and moving ref-name alias, then import and/or re-point. Convergence = revision placed AND alias current." "Python"
                    ucIntake = component "intake" "Fetch, verify the revision, stamp, package into a byte-reproducible zip and put one skill as an OCI artifact. Refuses a ref that moved between the caller's resolve and this fetch, before anything is pushed." "Python"
                    ucPurge = component "purge (use case)" "Catalog-walks the registry for pending-deletion referrers; asks the usage oracle per digest; hard-deletes only the safely-unused. Fail-closed: oracle error ⇒ nothing purged." "Python"
                    ucAudit = component "audit (use case)" "Catalog-walks the registry and classifies each image as stamped or not; emits the coverage report + exit code." "Python"
                    ucAttach = component "attach (use case)" "Resolves the subject digest, summarizes the ingested scan report, and puts it as a stamped OCI referrer." "Python"
                    ucGc = component "gc (use case)" "Catalog-walks the registry for scan-result referrers and collects the superseded ones (keep N newest per (tool, format) older than a grace window); pure temporal decision, no usage oracle. Dry-run by default." "Python"
                    ucVerify = component "verify (use case)" "Resolves stamp annotations, SBOM referrers, and verified scan predicates for a digest; delegates to domain evaluate(); returns a VerifyReport. Read-only." "Python"
                    ucScanWorker = component "scan_worker" "handle_reservation + make_scan_and_attach: claims a digest from the queue, runs the scan-and-attach pipeline, and ACKs or NACKs the reservation." "Python"
                    ucReport = component "report" "RunReport contract + worst-wins exit code." "Pydantic"
                    ucRegistrySession = component "registry_session" "Shared use-case helper: configure TLS/CA + login once per host (idempotent via caller-owned logged_in set). Used by reconcile, audit, and attach." "Python"
                }
                group "Domain (pure)" {
                    domSchema = component "policy schema" "MirrorPolicy model + published JSON Schema." "Pydantic" "Domain"
                    domPlanning = component "planning pipeline" "Tag selection, aliases, semver, variants, expand, reconcile plan, collision, sharding, retention." "Pure Python" "Domain"
                    domTransform = component "transform engine" "Pluggable transform-step vocabulary: base, steps, registry, render, version." "Pure Python" "Domain"
                    domStamp = component "provenance stamp" "Builds the OCI-standard + io.knock.* provenance annotations." "Pure Python" "Domain"
                    domCoverage = component "coverage" "Pure stamp-presence predicate: is the image knock-stamped?" "Pure Python" "Domain"
                    domAttestation = component "attestation predicate" "Builds the in-toto transform Statement (predicate type /v1)." "Pure Python" "Domain"
                    domScan = component "scan ingestion" "Detects the scan-report format, parses it (e.g. SARIF), and summarizes it into stamp annotations." "Pure Python" "Domain"
                    domSbom = component "SBOM facts" "SBOM format media-types and referrer annotation builder (domain/sbom.py)." "Pure Python" "Domain"
                    domVerify = component "verify logic" "Pure gate evaluation: Requirement enum; evaluate() maps stamp/sbom presence and verified scan predicates to a VerifyReport (pass/fail + detail per requirement). Reuses gate_breached; fail-closed on missing/stale/unverifiable attestation." "Pure Python" "Domain"
                    domScanQueue = component "scan queue logic" "Pure scan decision logic (domain/scan_queue.py): determines whether a placed digest needs scanning, deduplicates, and builds the ScanOutcome payload. No I/O. Under the ≥90 % domain gate + mypy --strict." "Pure Python" "Domain"
                    domPackaging = component "packaging planner" "Pure archive plan over a walked tree (domain/packaging.py): refuses symlinks, escapes, backslashes, collisions, archives over 100 MiB, and trees with no recognisable plugin layout. Excludes VCS metadata at the walker, so every source inherits the protection." "Pure Python" "Domain"
                }
                group "Ports" {
                    portRegistry = component "RegistryPort" "OCI registry ops: list, inspect, copy, annotate, delete, login, referrer list/put/delete; list_repositories (catalog walk for purge)." "typing.Protocol" "Port"
                    portBuilder = component "ImageBuilderPort" "Build and push an image from a Dockerfile + context." "typing.Protocol" "Port"
                    portReporter = component "Reporter" "In-flight reconcile event journal." "typing.Protocol" "Port"
                    portClock = component "ClockPort" "Injectable now()." "typing.Protocol" "Port"
                    portUsageOracle = component "UsageOraclePort" "Was this image digest seen in prod since a given timestamp? (stateless, point-in-time query)." "typing.Protocol" "Port"
                    portAttestor = component "AttestorPort" "Sign an in-toto Statement (DSSE) + attach it as an OCI referrer. Verify: cosign verify-attestation over a predicate type → list[VerifiedPredicate]." "typing.Protocol" "Port"
                    portSbomGenerator = component "SbomGeneratorPort" "Generate package-level SBOM(s) for a placed image by digest; returns one document per format." "typing.Protocol" "Port"
                    portQueue = component "QueuePort" "Enqueue a placed-image digest for scanning; claim the next Reservation (XAUTOCLAIM semantics); ACK or NACK. Optional — only present when knock-oci[scan] is installed." "typing.Protocol" "Port"
                    portSource = component "SourcePort" "Non-registry upstream ingestion. resolve(origin, ref) returns the immutable revision WITHOUT materialising a tree, so the plan phase never clones; fetch(origin, ref, workdir) materialises it. The two must agree for the same inputs. Generic on purpose: git is the first such source, not the only one." "typing.Protocol" "Port"
                    portArchiver = component "ArchiverPort" "Walks a fetched tree and writes a byte-reproducible zip from a planned entry list. Deliberately never faked — its defects are properties of a real filesystem." "typing.Protocol" "Port"
                }
                group "Adapters" {
                    adRegctl = component "RegctlAdapter" "Drives the regctl CLI via subprocess." "regctl" "Adapter"
                    adBuildkit = component "BuildkitAdapter" "Drives buildctl against buildkitd via subprocess (provenance attestation; no SBOM — superseded by SyftAdapter)." "buildctl" "Adapter"
                    adReporter = component "StructlogReporter" "Writes the event journal to stderr." "structlog" "Adapter"
                    adClock = component "SystemClock" "OS wall clock." "stdlib" "Adapter"
                    adUsageOracle = component "CommandUsageAdapter" "Shells out to KNOCK_USAGE_ORACLE_CMD; passes digest + idle window via stdin (JSON); expects {last_seen} on stdout." "subprocess" "Adapter"
                    adCosign = component "CosignAdapter" "Drives the cosign CLI via subprocess (keyless | kms | key)." "cosign" "Adapter"
                    adSyft = component "SyftAdapter" "Drives the syft CLI via subprocess; config-file auth/TLS; lazy binary resolution." "syft" "Adapter"
                    adRedisStreams = component "RedisStreamsAdapter" "Drives redis-py 8 Streams (XADD / XAUTOCLAIM / XACK / XTRIM); consumer-group semantics; RESP3. Part of the knock-oci[scan] optional extra." "redis-py 8" "Adapter"
                    adGit = component "GitAdapter" "Drives the git CLI via subprocess: ls-remote to resolve a ref without cloning, depth-1 fetch + checkout to materialise the tree. Refuses a non-empty workdir; '--' before positionals blocks option injection from a policy field." "git" "Adapter"
                    adArchiver = component "LocalArchiver" "Walks a real filesystem tree and writes the reproducible zip (fixed date_time, explicit ZipInfo, atomic replace)." "stdlib" "Adapter"
                }
                config = component "config" "Reads KNOCK_* settings + roster resolvers — the only os.environ reader." "Pydantic Settings"

                # Coarse layer components — rendered only by the synthetic "Hexagon" view
                # (the detailed Component view excludes them). They sit alongside the
                # fine-grained components above, one abstraction level up.
                layCli = component "cli" "Thin Typer entrypoint + composition root (_di)." "driving side" "Layer"
                layUc = component "use cases" "Orchestrates the reconcile flow; depends only on ports." "application" "Layer"
                layDomain = component "domain" "Pure core: policy schema, planning, transforms, provenance — no I/O." "pure core" "Layer,Domain"
                layPorts = component "ports" "typing.Protocol interfaces — the hexagon's boundary." "boundary" "Layer,Port"
                layAdapters = component "adapters" "Implement the ports; reach the external systems (subprocess / stdlib)." "driven side" "Layer,Adapter"
            }
        }

        redisBroker = softwareSystem "Redis (Streams)" "Message broker for the scan pipeline: reconcile enqueues placed-image digests; scan workers claim reservations via XAUTOCLAIM (consumer groups). Optional — only required when the knock-oci[scan] extra is installed." "External"

        sourceRegistries = softwareSystem "Source Registries" "External public OCI registries (Docker Hub, Quay, GHCR) the images originate from." "External"
        sourceRepositories = softwareSystem "Source Repositories (git)" "External git hosts holding source-derived artifacts — agent skills and other content that is published as a repository rather than as an image. knock resolves a ref to an immutable commit and fetches that tree; it never writes back." "External"
        destRegistries = softwareSystem "Destination Registries" "The organization's private OCI registries — any dist-spec registry (Harbor, Zot, …); destination for the stamped images, addressed generically via regctl." "External"
        buildkit = softwareSystem "BuildKit" "OCI build engine knock drives to rebuild and harden images." "External"
        packageMirror = softwareSystem "Internal Package Mirror" "The organization's internal apt/apk mirror; the hardening rebuild rewrites the image's package sources to it." "External"
        observability = softwareSystem "Observability / CMDB" "The organization's existing query stack; reads the provenance stamp to answer blast-radius questions during an incident." "External,Downstream"
        reaper = softwareSystem "Deletion reaper (external)" "Verifies prod usage and purges tags knock marked pending-deletion." "External,Downstream"
        usageOracle = softwareSystem "Usage oracle / observability" "Answers 'was this image's content seen in production lately?' (e.g. Datadog). Queried point-in-time by knock purge; never owned by knock." "External"
        signingService = softwareSystem "Signing / Key service" "KMS or Fulcio (keyless CA) that knock's attestor uses to sign in-toto attestations (DSSE). Trust is org configuration, not baked in." "External"
        transparencyLog = softwareSystem "Transparency log (Rekor)" "Optional append-only signature log; blank in air-gapped orgs. knock can point at one but never deploys it." "External,Downstream"
        upstreamScanner = softwareSystem "Upstream Scanner" "Produces vulnerability / EOL scan reports (CI pipeline, registry-native scanner, or scan service). knock ingests the report; it never calls the scanner." "External"
        argocd = softwareSystem "ArgoCD" "GitOps controller: the App-of-Apps that syncs the knock install from git. This IS the reference deployment — and the demo on kind. The kubectl apply -k overlays/local path is the inner-loop escape hatch, not a separate blueprint." "External"

        platformEng -> knock "Configures the hardening policy + registry roster, runs / schedules reconcile" "CLI"
        productTeam -> knock "Declares its imports as MirrorPolicy files" "YAML"
        knock -> sourceRegistries "Lists tags, inspects digests, copies images" "regctl"
        knock -> sourceRepositories "Resolves a ref to a commit; fetches the tree at that revision" "git"
        knock -> destRegistries "Reads mirror state; copies, stamps, retags, deletes" "regctl (dist-spec)"
        knock -> buildkit "Submits the hardening rebuild (internal CA trust, package mirror)" "buildctl"
        buildkit -> packageMirror "Pulls packages during the hardening rebuild" "apt / apk"
        productTeam -> destRegistries "Pulls the hardened images" "docker pull (OCI)"
        observability -> destRegistries "Reads provenance stamps on images" "scan / API" "DataCoupling"
        incidentResponder -> observability "Queries blast-radius (at CVE time)" "Query UI"
        reaper -> destRegistries "Discovers pending-deletion referrers, verifies usage, purges" "OCI referrers API" "DataCoupling"
        knock -> usageOracle "Queries prod usage at purge time (knock purge)" "subprocess (KNOCK_USAGE_ORACLE_CMD)"
        knock -> signingService "Signs in-toto attestations (DSSE)" "cosign"
        knock -> transparencyLog "Records the signature (optional; blank => skipped)" "cosign / rekor"
        upstreamScanner -> knock "Produces scan reports ingested by" "SARIF / file"
        knock -> redisBroker "Enqueues placed-image digests; scan workers claim reservations (knock-oci[scan] optional)" "redis-py 8 / RESP3 Streams"
        argocd -> knock "Syncs the install manifests from git into the cluster (App-of-Apps reference)" "GitOps"

        # Component-level relationships — the source of truth for the Component view.
        # Structurizr implies the container/system-level edges for the views above
        # (the explicit system-level edges already declared suppress duplicate implied ones).
        platformEng -> cliReconcile "Configures policy + roster, runs / schedules reconcile" "CLI"
        productTeam -> ucLoader "Provides MirrorPolicy files" "YAML"

        cliMain -> cliReconcile "Registers the command" "Typer"
        cliMain -> cliPurge "Registers the command" "Typer"
        cliMain -> cliAudit "Registers the command" "Typer"
        cliMain -> cliAttach "Registers the command" "Typer"
        cliMain -> cliGc "Registers the command" "Typer"
        cliMain -> cliVerify "Registers the command" "Typer"
        cliMain -> cliScan "Registers the command group (knock-oci[scan])" "Typer"
        cliAudit -> cliDi "Builds the composition root" "Python"
        cliAudit -> ucAudit "Runs the audit" "Python"
        ucAudit -> domCoverage "Classifies each image" "Python"
        ucAudit -> portRegistry "Catalog-walks + reads annotations" "Protocol"
        ucAudit -> ucRegistrySession "Configures TLS/CA + login per registry" "Python"
        cliAttach -> cliDi "Builds the composition root" "Python"
        cliAttach -> ucAttach "Runs the ingest" "Python"
        upstreamScanner -> ucAttach "Provides scan reports" "SARIF / file"
        ucAttach -> domScan "Detects format, parses & summarizes the report" "Python"
        ucAttach -> portRegistry "Resolves the subject digest + puts the scan referrer" "Protocol"
        ucAttach -> portClock "Reads now()" "Protocol"
        ucAttach -> ucRegistrySession "Configures TLS/CA + login per registry (host-match or --registry override)" "Python"
        cliPurge -> cliDi "Builds the composition root" "Python"
        cliPurge -> ucPurge "Runs the purge" "Python"
        cliPurge -> portClock "Reads now()" "Protocol"
        cliGc -> cliDi "Builds the composition root" "Python"
        cliGc -> ucGc "Runs the gc" "Python"
        cliGc -> portClock "Reads now()" "Protocol"
        cliVerify -> cliDi "Builds the composition root" "Python"
        cliVerify -> ucVerify "Runs the gate evaluation" "Python"
        cliVerify -> portClock "Reads now()" "Protocol"
        cliReconcile -> cliDi "Builds the composition root" "Python"
        cliReconcile -> ucLoader "Loads policies" "Python"
        cliReconcile -> ucReconcile "Runs reconciliation" "Python"
        cliReconcile -> cliRender "Renders the report" "Python"
        cliReconcile -> portClock "Reads now()" "Protocol"
        cliDi -> config "Reads KNOCK_* settings" "Pydantic Settings"
        cliDi -> adRegctl "Wires" "DI"
        cliDi -> adBuildkit "Wires" "DI"
        cliDi -> adReporter "Wires" "DI"
        cliDi -> adClock "Wires" "DI"
        cliDi -> adUsageOracle "Wires" "DI"
        cliDi -> adGit "Wires" "DI"
        cliDi -> adArchiver "Wires" "DI"

        ucLoader -> domSchema "Parses MirrorPolicy" "Pydantic"
        ucReconcile -> ucReport "Builds the RunReport" "Python"
        ucReconcile -> domPlanning "Enforces the ownership invariant, shards, and detects dest-repo + alias collisions across every planner" "Python"
        ucReconcile -> portReporter "Uses" "Protocol"

        # The driver holds planners only through the protocol; the two implementations
        # are what it constructs. Edge direction follows the dependency, not the call.
        ucReconcile -> ucPolicyPlanner "Dispatches plan / apply through" "Protocol"
        ucReconcileRegistry -> ucPolicyPlanner "Implements" "Protocol"
        ucReconcileGit -> ucPolicyPlanner "Implements" "Protocol"

        ucReconcileRegistry -> domPlanning "Computes the import / update / delete plan" "Python"
        ucReconcileRegistry -> domTransform "Renders & versions transforms" "Python"
        ucReconcileRegistry -> domStamp "Builds provenance annotations" "Python"
        ucReconcileRegistry -> portRegistry "Uses" "Protocol"
        ucReconcileRegistry -> portBuilder "Uses" "Protocol"
        ucReconcileRegistry -> portReporter "Uses" "Protocol"
        ucReconcileRegistry -> ucRegistrySession "Configures TLS/CA + login per registry" "Python"

        ucReconcileGit -> portSource "resolve(): the plan-phase read that keeps --dry-run from cloning" "Protocol"
        ucReconcileGit -> portRegistry "Lists the destination's tags; reads the alias stamp; copies the alias onto the revision tag" "Protocol"
        ucReconcileGit -> portReporter "Uses" "Protocol"
        ucReconcileGit -> ucRegistrySession "Configures TLS/CA + login per registry" "Python"
        ucReconcileGit -> ucIntake "Places one revision (passes the planned revision as expected_revision)" "Python"

        ucIntake -> portSource "fetch(): materialises the tree at the ref" "Protocol"
        ucIntake -> portArchiver "Walks the tree and writes the reproducible zip" "Protocol"
        ucIntake -> domPackaging "Plans the archive; refuses an unsafe or oversized tree" "Python"
        ucIntake -> domStamp "Builds the base-less source-derived provenance annotations" "Python"
        ucIntake -> portRegistry "put_artifact: pushes the stamped OCI artifact" "Protocol"

        ucPurge -> portRegistry "Lists repos + referrers; hard-deletes purged tags" "Protocol"
        ucPurge -> portUsageOracle "Was this digest seen in prod?" "Protocol"
        ucPurge -> portClock "Computes idle window" "Protocol"

        ucGc -> portRegistry "Lists repos + scan referrers; deletes superseded ones" "Protocol"
        ucGc -> ucRegistrySession "Configures TLS/CA + login per registry" "Python"
        ucVerify -> domVerify "Evaluates stamp / sbom / scan-pass outcomes" "Python"
        ucVerify -> portRegistry "Reads annotations + lists SBOM referrers" "Protocol"
        ucVerify -> portAttestor "verify-attestation: returns list[VerifiedPredicate]" "Protocol"
        ucVerify -> portClock "Reads now() for freshness" "Protocol"
        ucVerify -> ucRegistrySession "Configures TLS/CA + login per registry" "Python"

        ucRegistrySession -> portRegistry "Calls configure_registry + login" "Protocol"

        adRegctl -> portRegistry "Implements" "Protocol"
        adBuildkit -> portBuilder "Implements" "Protocol"
        adReporter -> portReporter "Implements" "Protocol"
        adClock -> portClock "Implements" "Protocol"
        adUsageOracle -> portUsageOracle "Implements" "Protocol"
        adGit -> portSource "Implements" "Protocol"
        adArchiver -> portArchiver "Implements" "Protocol"

        adRegctl -> sourceRegistries "Lists tags, inspects digests, copies images" "regctl"
        adRegctl -> destRegistries "Reads mirror state; copies, stamps, retags, deletes" "regctl (dist-spec)"
        adBuildkit -> buildkit "Submits the hardening rebuild (internal CA trust, package mirror)" "buildctl"
        adUsageOracle -> usageOracle "Queries prod usage (KNOCK_USAGE_ORACLE_CMD)" "subprocess (stdin/stdout JSON)"
        adGit -> sourceRepositories "ls-remote to resolve the ref; depth-1 fetch of the tree" "git"

        ucReconcileRegistry -> domAttestation "Builds the transform Statement (rebuild path)" "Python"
        ucReconcileRegistry -> portAttestor "Signs the transform + SBOM predicates (both paths)" "Protocol"
        ucReconcileRegistry -> domSbom "Builds SBOM referrer annotations (both paths)" "Python"
        cliDi -> adCosign "Wires" "DI"
        adCosign -> portAttestor "Implements" "Protocol"
        adCosign -> signingService "Signs attestations (DSSE)" "cosign"
        adCosign -> transparencyLog "Records the signature (optional)" "cosign / rekor"

        cliDi -> adSyft "Wires" "DI"
        adSyft -> portSbomGenerator "Implements" "Protocol"
        ucReconcileRegistry -> portSbomGenerator "Generates SBOM(s) after placing each image (both paths)" "Protocol"
        adSyft -> destRegistries "Scans the placed image by digest" "syft"

        cliScan -> cliDi "Builds the composition root" "Python"
        cliScan -> ucScanWorker "Runs reserve / attach / reaper" "Python"
        cliScan -> portQueue "Enqueues (enqueue sub-command)" "Protocol"
        ucScanWorker -> domScanQueue "Pure scan decision logic" "Python"
        ucScanWorker -> portQueue "Claims and ACKs/NACKs reservations" "Protocol"
        ucScanWorker -> ucAttach "Runs the scan-and-attach pipeline per reservation" "Python"
        cliDi -> adRedisStreams "Wires (knock-oci[scan] only)" "DI"
        adRedisStreams -> portQueue "Implements" "Protocol"
        adRedisStreams -> redisBroker "XADD / XAUTOCLAIM / XACK / XTRIM via RESP3" "redis-py 8"

        # Coarse hexagon relationships — rendered only in the synthetic "Hexagon" view.
        platformEng -> layCli "Runs / schedules reconcile" "CLI"
        productTeam -> layCli "Provides MirrorPolicy files" "YAML"
        layCli -> layUc "Invokes use cases" "Python"
        layCli -> config "Reads settings" "Pydantic Settings"
        layCli -> layAdapters "Wires (composition root)" "DI"
        layUc -> layDomain "Orchestrates pure logic" "Python"
        layUc -> layPorts "Depends on" "Protocol"
        layAdapters -> layPorts "Implement" "Protocol"
        layAdapters -> sourceRegistries "Lists, inspects, copies images" "regctl"
        layAdapters -> sourceRepositories "Resolves refs; fetches source trees" "git"
        layAdapters -> destRegistries "Copies, stamps, retags, deletes" "regctl"
        layAdapters -> buildkit "Submits the hardening rebuild" "buildctl"
        layAdapters -> usageOracle "Queries prod usage (purge)" "subprocess"
        layAdapters -> signingService "Signs attestations" "cosign"
        layAdapters -> transparencyLog "Records the signature (optional)" "cosign"
        layAdapters -> redisBroker "Enqueues / claims scan reservations (knock-oci[scan] optional)" "redis-py 8"

        # Deployments — one environment per worked example, each scoped to the kind overlay
        # that runs it (the demo IS the blueprint), plus the production blueprint. The old
        # single "Reference (kind)" view merged every overlay into one cramped diagram; these
        # split it by example so each reads cleanly and carries its own overlay facts.
        # See docs/superpowers/specs/2026-06-11-reference-deployment-design.md, deploy/overlays/,
        # and docs/examples/. Instance↔instance edges (knock→source/dest/buildkit,
        # buildkit→packageMirror) are auto-replicated from the model; only the infrastructure-node
        # edges are declared per environment.

        # ── Greenfield — the full GitOps Argo App-of-Apps (Reference B, advanced). On kind it
        #    is the demo (`make demo`); the same App-of-Apps adopts to a real cluster (swap
        #    repo, vault, registry, image). Thesis-minimum operators: ESO + OpenBao (wave 0),
        #    knock + buildkitd (wave 1). KEDA + Prometheus autoscaling is an optional add-on
        #    (components/keda-buildkitd), deliberately NOT on this path.
        refEnv = deploymentEnvironment "Greenfield — full GitOps platform (Reference B, advanced)" {
            deploymentNode "Git host" "github.com/trivoallan/knock (or a fork) — deploy/argocd/" "Git server" {
                rfRepo = infrastructureNode "Manifests repo" "root.yaml + apps/ + sources/* — a merged PR is the front door" "git / GitOps"
                rfPolicyRepo = infrastructureNode "Policy repo (org)" "POLICY_REPO_URL — git-sync clones it; POLICY_DIR=docs/examples/reference (busybox copy + debian rebuild). ArgoCD never touches policies." "git / GitOps"
            }
            deploymentNode "Kubernetes cluster" "kind (the demo) or a real cluster — same manifests (anti-drift)" "Kubernetes" {
                deploymentNode "namespace: argocd" "ArgoCD controller" "Namespace" {
                    rfRoot = infrastructureNode "Application: knock-root" "App-of-Apps; syncs the apps/ children from git (no demo/prod split)" "ArgoCD"
                    rfArgo = softwareSystemInstance argocd
                }
                deploymentNode "namespace: knock" "knock workloads (wave 1)" "Namespace" {
                    deploymentNode "CronJob: knock-reconcile" "BUILDKIT_HOST wired via config (no CronJob patch). On kind: image knock:dev; reconciles the reference policy (copy + rebuild)" "Kubernetes CronJob" {
                        rfKnock = containerInstance knockCli
                        rfGit = infrastructureNode "git-sync sidecar" "Clones the policy repo into /policies" "git-sync"
                    }
                    deploymentNode "Deployment: buildkitd (own app)" "Rebuild add-on (rootless build engine) — its own ArgoCD Application" "Kubernetes Deployment" {
                        rfBuild = softwareSystemInstance buildkit
                    }
                    rfEsObj = infrastructureNode "ExternalSecret + ClusterSecretStore" "knock-secret-store → OpenBao (ESO vault provider)" "ExternalSecret"
                    rfBlast = infrastructureNode "Job: blast-radius" "BLAST_REPOS=demo/busybox demo/debian — reads stamps, answers the CVE-time query" "regctl"
                    rfGc = infrastructureNode "CronJob: knock-gc" "Weekly knock gc --apply — collects superseded scan referrers (keep=2/older-than=30d). No git-sync/policies; walks the roster only." "Kubernetes CronJob"
                    rfDt = infrastructureNode "Deployment: dependency-track (own app)" "Worked-example SBOM consumer (apiserver + frontend, embedded H2) — its own ArgoCD Application (ADR 0035). Currency layer: package-level blast-radius. Fed the CycloneDX SBOM referrer knock attaches (KNOCK_SBOM_FORMATS), uploaded by publish-sbom." "Kubernetes Deployment"
                    rfPublishSbom = infrastructureNode "Job: knock-publish-sbom" "Fetches the CycloneDX SBOM referrer knock attaches to each placed image and uploads it to DT. regctl + python, no conversion. Twin of the blast-radius consumer." "Kubernetes Job"
                    rfScanAttach = infrastructureNode "Job: knock-scan-attach" "Off-the-shelf grype evaluates the SBOM referrer knock attached (grype sbom:, no registry creds); knock attach binds the SARIF as a signed referrer on the same digest. regctl + grype + knock, no derived image. grype pulls its CVE DB from the internet (air-gapped => mirror)." "Kubernetes Job"
                    rfMarkedWorkloads = deploymentNode "marked workloads (team-a/b/c)" "pause pods annotated with the placed/bypass digest — the runtime stand-in (namespaces-as-clusters)" "Kubernetes Deployment" {
                        rfPausePods = infrastructureNode "pause pods" "One pod per namespace (team-a/b/c), annotated with the placed/bypass digest; queried by blast-radius via kube API." "Kubernetes Pod"
                    }
                }
                deploymentNode "namespace: external-secrets" "ESO operator (wave 0)" "Namespace" {
                    rfEso = infrastructureNode "External Secrets Operator" "Helm child; materializes the registry roster Secret" "ESO"
                }
                deploymentNode "namespace: openbao" "Secret backend (wave 0)" "Namespace" {
                    rfBao = infrastructureNode "OpenBao" "Helm child (dev mode on kind); holds knock/registries" "OpenBao"
                }
                deploymentNode "namespace: registry" "Throwaway Zot — OCI registry + built-in web UI (make registry-ui); applied out-of-band by make demo, ArgoCD does not manage it" "Namespace" {
                    rfDest = softwareSystemInstance destRegistries
                }
            }
            deploymentNode "Internet / org network" "External to the cluster" "Network" {
                rfSrc = softwareSystemInstance sourceRegistries
            }
            rfArgo -> rfRepo "Pulls manifests (App-of-Apps)" "git" "DataCoupling"
            rfRoot -> rfKnock "Syncs the knock install" "ArgoCD"
            rfRoot -> rfBuild "Syncs the buildkitd app" "ArgoCD"
            rfEso -> rfBao "Reads knock/registries" "vault API" "DataCoupling"
            rfEsObj -> rfEso "Requests the roster Secret" "ESO"
            rfKnock -> rfEsObj "Reads the registry roster" "env (secretRef)" "DataCoupling"
            rfGit -> rfPolicyRepo "Pulls policies" "git"
            rfBlast -> rfDest "Reads provenance stamps" "regctl" "DataCoupling"
            rfScanAttach -> rfDest "Fetches the SBOM referrer; attaches grype's SARIF" "regctl" "DataCoupling"
            rfBlast -> rfPausePods "Lists pods, joins digest -> cluster (kube API)" "kubectl / kube API"
            rfGc -> rfDest "Collects superseded scan referrers" "regctl" "DataCoupling"
            rfPublishSbom -> rfDest "Fetches the CycloneDX SBOM referrer" "regctl" "DataCoupling"
            rfPublishSbom -> rfDt "Uploads the SBOM" "Dependency-Track API" "DataCoupling"
        }

        # ── Brownfield — drop-in to an existing intake (make demo-mongobleed / make local).
        #    Self-contained: kubectl apply -k, plain Zot, no Argo/ESO. Reconciles the SAME
        #    reference policy (busybox copy + debian rebuild) and renders local, uncommitted
        #    manifests. This is the brownfield-simple headline runtime.
        localEnv = deploymentEnvironment "Brownfield — drop-in to existing intake (make demo-mongobleed / make local)" {
            deploymentNode "Operator host" "Laptop / CI runner: runs kind, holds the policy clone" "macOS / Linux" {
                loRepo = infrastructureNode "Policy repo" "docs/examples/reference — busybox copy + debian rebuild (git-sync'd)" "git / GitOps"
                deploymentNode "kind cluster" "Single-node Kubernetes — overlay local (self-contained: buildkitd, no operators)" "kind" {
                    deploymentNode "namespace: knock" "knock workloads" "Namespace" {
                        deploymentNode "CronJob: knock-reconcile" "Suspended; one-shot via make local. Image knock:dev · team=platform · POLICY_DIR=docs/examples/reference" "Kubernetes CronJob" {
                            loKnock = containerInstance knockCli
                            loGit = infrastructureNode "git-sync sidecar" "Clones the policy repo into /policies" "git-sync"
                        }
                        deploymentNode "Deployment: buildkitd" "Rootless build engine; pushes to the plain-HTTP Zot via registry.insecure derived from the roster tls_verify (generic component, no daemon config)" "Kubernetes Deployment" {
                            loBuild = softwareSystemInstance buildkit
                        }
                        loSecret = infrastructureNode "Secret: knock-registries" "Plain secret roster (no operators) — the inner-loop escape hatch" "Secret"
                        loBlast = infrastructureNode "Job: blast-radius" "BLAST_REPOS=demo/busybox demo/debian" "regctl"
                        loGc = infrastructureNode "CronJob: knock-gc" "Suspended (like reconcile); fired on demand. knock gc --apply over the roster." "Kubernetes CronJob"
                        loDt = infrastructureNode "Deployment: dependency-track (own app)" "Worked-example SBOM consumer (apiserver + frontend, embedded H2) — (ADR 0035). Currency layer: package-level blast-radius. Fed the CycloneDX SBOM referrer knock attaches (KNOCK_SBOM_FORMATS), uploaded by publish-sbom." "Kubernetes Deployment"
                        loPublishSbom = infrastructureNode "Job: knock-publish-sbom" "Fetches the CycloneDX SBOM referrer knock attaches to each placed image and uploads it to DT. regctl + python, no conversion. Twin of the blast-radius consumer." "Kubernetes Job"
                        loScanAttach = infrastructureNode "Job: knock-scan-attach" "grype (off-the-shelf) on the SBOM referrer => knock attach the SARIF. No derived image, no registry creds for grype; grype pulls its CVE DB from the internet (air-gapped => mirror)." "Kubernetes Job"
                        loMarkedWorkloads = deploymentNode "marked workloads (team-a/b/c)" "pause pods annotated with the placed/bypass digest — the runtime stand-in (namespaces-as-clusters)" "Kubernetes Deployment" {
                            loPausePods = infrastructureNode "pause pods" "One pod per namespace (team-a/b/c), annotated with the placed/bypass digest; queried by blast-radius via kube API." "Kubernetes Pod"
                        }
                    }
                    deploymentNode "namespace: registry" "Throwaway Zot — plain HTTP; copied + rebuilt images pushed here; built-in web UI (make registry-ui)" "Namespace" {
                        loDest = softwareSystemInstance destRegistries
                    }
                }
            }
            deploymentNode "Internet" "Public registries, external to the cluster" "Network" {
                loSrc = softwareSystemInstance sourceRegistries
            }
            loGit -> loRepo "Pulls policies" "git"
            loBlast -> loDest "Reads provenance stamps" "regctl" "DataCoupling"
            loBlast -> loPausePods "Lists pods, joins digest -> cluster (kube API)" "kubectl / kube API"
            loGc -> loDest "Collects superseded scan referrers" "regctl" "DataCoupling"
            loScanAttach -> loDest "Fetches the SBOM referrer; attaches grype's SARIF" "regctl" "DataCoupling"
            loPublishSbom -> loDest "Fetches the CycloneDX SBOM referrer" "regctl" "DataCoupling"
            loPublishSbom -> loDt "Uploads the SBOM" "Dependency-Track API" "DataCoupling"
            loKnock -> loSecret "Reads the registry roster" "env (secretRef)" "DataCoupling"
        }

        # ── Showcase — the public proof (ADR 0046). A third way knock runs: a CI runner, no
        #    Kubernetes and no ArgoCD. A scheduled GitHub Actions workflow in a separate thin
        #    repo (knock-examples) checks the SAME reference policies out of this repo at a
        #    pinned ref — never copied, so they cannot drift — and publishes stamped, SBOM-
        #    carrying, keyless-signed images to GHCR for anyone to verify. Coverage (audit /
        #    gc / purge) is absent by design: GHCR does not implement the OCI catalog API.
        showcaseEnv = deploymentEnvironment "Showcase — public proof (GitHub Actions -> GHCR)" {
            deploymentNode "GitHub Actions runner" "ubuntu-latest; weekly showcase run + nightly canary (throwaway namespace, non-blocking)" "GitHub Actions" {
                shPolicies = infrastructureNode "Pinned policy checkout" "actions/checkout of this repo at KNOCK_VERSION -> docs/examples/reference (busybox copy + debian-tz rebuild). Never copied into the showcase repo: the visitor verifies the exact file on the docs site." "git"
                deploymentNode "container: knock" "ghcr.io/<owner>/knock:KNOCK_VERSION, pinned in knock.env and bumped by Renovate (the canary builds from main instead)" "Docker" {
                    shKnock = containerInstance knockCli
                }
                deploymentNode "container: buildkitd" "moby/buildkit side container (BUILDKIT_HOST); needed by the debian-tz rebuild path. Pushes on its own behalf, so the GHCR docker config must be mounted for it." "Docker" {
                    shBuild = softwareSystemInstance buildkit
                }
                shBypass = infrastructureNode "Bypass push" "regctl image copy of an image that never came through the front door — the manual counter-example the README's commands fail on." "regctl"
                shVerify = infrastructureNode "verify.sh" "The README's own commands, replayed against what was just published: read the stamp, list the SBOM referrer, verify the attestation. knock has no transaction, so this is what stops a half-stamped publish from going public." "regctl / cosign"
            }
            deploymentNode "GitHub / Sigstore" "Public services backing the proof" "Internet" {
                shGhcr = softwareSystemInstance destRegistries
                shSigning = softwareSystemInstance signingService
                shRekor = softwareSystemInstance transparencyLog
            }
            deploymentNode "Internet" "Public upstreams (authenticated: a Docker Hub PAT, else the shared runner IPs are throttled)" "Network" {
                shSrc = softwareSystemInstance sourceRegistries
            }
            # The knock -> upstream / GHCR / buildkit / Fulcio / Rekor edges are implied from the
            # static model (adRegctl, adBuildkit, adCosign), exactly as in the other two
            # environments; only the showcase-specific nodes need explicit relationships.
            shKnock -> shPolicies "Reads the pinned policies" "filesystem" "DataCoupling"
            shBypass -> shGhcr "Pushes an unstamped, unsigned image" "regctl"
            shVerify -> shGhcr "Re-reads the stamp and the SBOM referrer" "regctl" "DataCoupling"
            shVerify -> shRekor "Verifies the attestation identity" "cosign" "DataCoupling"
        }
    }

    views {
        systemLandscape "Landscape" "knock in its enterprise context, through to incident-time blast-radius." {
            include *
            autolayout lr
        }

        systemContext knock "Context" "knock and the systems it integrates with directly." {
            include *
            autolayout lr
        }

        container knock "Container" "knock as a single deployable CLI container, and the external systems it drives." {
            include *
            autolayout lr
        }

        component knockCli "Hexagon" "Synthetic hexagonal overview: cli → use cases → domain, with ports ← adapters making the dependency inversion explicit (use cases and adapters both point at the ports). The driven adapters reach the external systems." {
            include layCli layUc layDomain layPorts layAdapters config platformEng productTeam sourceRegistries destRegistries buildkit usageOracle signingService transparencyLog redisBroker
            autolayout lr
        }

        component knockCli "Component" "Inside the knock CLI: every fine-grained component of the hexagonal layers — cli, use cases, the pure domain (4 concerns), the ports, and the adapters that reach the external systems." {
            include *
            exclude layCli layUc layDomain layPorts layAdapters
            autolayout lr
        }

        # Three deployment views: the Argo reference (which is the demo), the local inner-loop
        # overlay, and the public showcase. The same kustomize base underlies the first two —
        # the demo IS the blueprint. The showcase shares neither: it is knock on a CI runner.
        deployment knock "Greenfield — full GitOps platform (Reference B, advanced)" "DeployReference" "The greenfield reference (Reference B): an Argo App-of-Apps that is both the production blueprint and the kind demo. ESO + OpenBao (wave 0), knock + buildkitd (wave 1); the reference policy (busybox copy + debian rebuild); a throwaway Zot (registry + built-in UI) applied out-of-band. KEDA/Prometheus autoscaling is an optional add-on, not on this path." {
            include *
            autolayout lr
        }
        deployment knock "Brownfield — drop-in to existing intake (make demo-mongobleed / make local)" "DeployLocal" "The brownfield headline runtime: kubectl apply -k, plain Zot, no Argo/ESO operators. Drop-in to an existing cluster intake. Reconciles the same reference policy (copy + rebuild) and renders local, uncommitted manifests." {
            include *
            autolayout lr
        }
        deployment knock "Showcase — public proof (GitHub Actions -> GHCR)" "DeployShowcase" "The public proof (ADR 0046): a scheduled GitHub Actions workflow in a separate thin repo runs the same reference policies — checked out here at a pinned ref, never copied — and publishes stamped, SBOM-carrying, keyless-signed images to GHCR. verify.sh replays the README's own commands against the result, so a half-stamped publish never goes public. No Kubernetes, no ArgoCD, and no coverage walk: GHCR serves no OCI catalog." {
            include *
            autolayout lr
        }

        styles {
            element "Element" {
                shape RoundedBox
                color #ffffff
            }
            element "Person" {
                shape Person
                background #52606d
            }
            element "Software System" {
                background #69707a
            }
            element "External" {
                background #69707a
            }
            element "Target" {
                background #1f6feb
            }
            element "Downstream" {
                background #0f766e
            }
            element "Infrastructure Node" {
                shape RoundedBox
                background #8a94a0
            }
            element "Container" {
                background #2563c9
                color #ffffff
            }
            element "Component" {
                shape RoundedBox
                background #cdd5df
                color #1f2933
            }
            element "Domain" {
                background #b6e3d4
                color #04342c
            }
            element "Port" {
                background #e9d8fd
                color #322659
            }
            element "Adapter" {
                background #fed7aa
                color #4a1b0c
            }
            element "Layer" {
                strokeWidth 6
            }
            relationship "Relationship" {
                routing Orthogonal
            }
            relationship "DataCoupling" {
                dashed true
            }
        }
    }

    configuration {
        scope softwaresystem
    }
}
