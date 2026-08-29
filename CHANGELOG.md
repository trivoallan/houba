# Changelog

## [0.9.3](https://github.com/trivoallan/knock/compare/v0.9.2...v0.9.3) (2026-08-29)


### Bug Fixes

* **domain:** keyless signing works with no Fulcio/Rekor configured ([#252](https://github.com/trivoallan/knock/issues/252)) ([e97031a](https://github.com/trivoallan/knock/commit/e97031a73a36ee20876031f1e783417d56252f52))


### Documentation

* **docs:** a how-to for verifying the publicly published stamp ([#251](https://github.com/trivoallan/knock/issues/251)) ([b9df4cf](https://github.com/trivoallan/knock/commit/b9df4cf882fb64b023f9e05cc58dfdcb2685fae1))

## [0.9.2](https://github.com/trivoallan/knock/compare/v0.9.1...v0.9.2) (2026-08-29)


### Bug Fixes

* **use_cases:** skip referrers-fallback tags in every tag walk ([#248](https://github.com/trivoallan/knock/issues/248)) ([3186a20](https://github.com/trivoallan/knock/commit/3186a20ccd26c81e95cbde295bd992e7b5522cb4))

## [0.9.1](https://github.com/trivoallan/knock/compare/v0.9.0...v0.9.1) (2026-08-29)


### Bug Fixes

* **ci:** publish the image again on a freshly cut release ([#246](https://github.com/trivoallan/knock/issues/246)) ([bb42af2](https://github.com/trivoallan/knock/commit/bb42af2fc94900642212ee9a2bcb1de6124b9ca8))

## [0.9.0](https://github.com/trivoallan/knock/compare/v0.8.0...v0.9.0) (2026-08-29)


### ⚠ BREAKING CHANGES

* rename houba → knock (knock-oci on PyPI; KNOCK_/io.knock contracts) ([#193](https://github.com/trivoallan/knock/issues/193))

### Features

* **cli:** houba audit --limit — bound the catalog walk for slice benchmarks ([#186](https://github.com/trivoallan/knock/issues/186)) ([b9a2b5a](https://github.com/trivoallan/knock/commit/b9a2b5a8a438e384e088091af73510b1e3c8c2b6))
* **cli:** houba scan command group as an optional houba[scan] extra (Door 1) ([#190](https://github.com/trivoallan/knock/issues/190)) ([c4b717b](https://github.com/trivoallan/knock/commit/c4b717baf7bd865011409a5e2c1a41022bf4bba7))
* **cli:** houba verify — read-side gate over signed scan, stamp, and SBOM ([#182](https://github.com/trivoallan/knock/issues/182)) ([6fd93ae](https://github.com/trivoallan/knock/commit/6fd93ae6fe781e52321ac0ee8fdb73053abd8393))
* **config:** target path-namespaced registries (GHCR, GitLab, Artifactory) ([#245](https://github.com/trivoallan/knock/issues/245)) ([bdd1a57](https://github.com/trivoallan/knock/commit/bdd1a57d2b8aadb2fec9552d2849210d0dad262c))
* **image:** platform-internal scan/SBOM pipeline — incremental, reconcile-fed reference deployment ([#187](https://github.com/trivoallan/knock/issues/187)) ([43538c5](https://github.com/trivoallan/knock/commit/43538c5292d49ccbcf6e429d4a0a730bc2a2b58f))
* **image:** rebuild the scan pipeline on Redis Streams (trim-safe, coverage-convergent, operator-triageable) ([#188](https://github.com/trivoallan/knock/issues/188)) ([c46d824](https://github.com/trivoallan/knock/commit/c46d824735b69a3642184f9a7f5f750b240658a8))


### Bug Fixes

* **deps:** patch minimatch ReDoS (GHSA-7r86-cg39-jmmj) in website ([#217](https://github.com/trivoallan/knock/issues/217)) ([51b3cef](https://github.com/trivoallan/knock/commit/51b3cef4fddc7d995762b5f99680a138bc1929c6))
* **use_cases:** pin the copy path to the digest it stamps ([#241](https://github.com/trivoallan/knock/issues/241)) ([6b1d4a4](https://github.com/trivoallan/knock/commit/6b1d4a469322a1f193882f4c0b5c58eaa689bb2a))


### Documentation

* **architecture:** make the Structurizr inspection gate green again ([#243](https://github.com/trivoallan/knock/issues/243)) ([55c56b5](https://github.com/trivoallan/knock/commit/55c56b58ef3efdcec970cde9081a0ac62ef0ac02))
* **docs:** pin the MirrorPolicy ⟂ Warehouse boundary in the Kargo gate spec ([#185](https://github.com/trivoallan/knock/issues/185)) ([11679a8](https://github.com/trivoallan/knock/commit/11679a8a694f69994b3c694206c2e2ec6ae38b35))
* **docs:** spec for Kargo promotion gate (houba as a third reader) ([#175](https://github.com/trivoallan/knock/issues/175)) ([238032e](https://github.com/trivoallan/knock/commit/238032e73ac40a78d310defd051c145e2a7e613d))
* **docs:** spec migration-parity demonstrable proof ([#179](https://github.com/trivoallan/knock/issues/179)) ([b18be28](https://github.com/trivoallan/knock/commit/b18be28a6e6d083d65b76e03b64f623f49350c8a))
* **explanation:** add Features-at-a-glance overview page ([#194](https://github.com/trivoallan/knock/issues/194)) ([ab90c87](https://github.com/trivoallan/knock/commit/ab90c871fde9e0a28a3d56bebef786bcc485a66c))
* **roadmap:** scope the near-term increment (post-0.8.0) ([#176](https://github.com/trivoallan/knock/issues/176)) ([b5aee30](https://github.com/trivoallan/knock/commit/b5aee30b36b665cad8200cd41700485ef52e4750))
* **specs:** draft spec for renaming houba → knock (clean break) ([#202](https://github.com/trivoallan/knock/issues/202)) ([1741793](https://github.com/trivoallan/knock/commit/1741793adb151ce611c813bb60c8bb7b82994f85))
* sync the CLI verb and layer module inventories with the code ([#242](https://github.com/trivoallan/knock/issues/242)) ([1dd19c1](https://github.com/trivoallan/knock/commit/1dd19c1b492fbea0a86e1f6951040c1e238bfe76))


### Code Refactoring

* rename houba → knock (knock-oci on PyPI; KNOCK_/io.knock contracts) ([#193](https://github.com/trivoallan/knock/issues/193)) ([11f58ec](https://github.com/trivoallan/knock/commit/11f58ecb08ecf457f6be57b215ee9e732ce7eb9b))

## [0.8.0](https://github.com/trivoallan/knock/compare/v0.7.0...v0.8.0) (2026-06-23)


### Features

* **deploy:** demonstrable blast-radius — owner + cluster join (demo glue, no knock-core) ([#156](https://github.com/trivoallan/knock/issues/156)) ([9554927](https://github.com/trivoallan/knock/commit/955492712193ee6166090480d0b6df530291c23b))
* **deploy:** make argocd-ui + distinct UI ports (registry 8080→8082) ([#158](https://github.com/trivoallan/knock/issues/158)) ([db0ab65](https://github.com/trivoallan/knock/commit/db0ab65d11dd36554263992ae4d8df6993bee57e))
* **domain:** SARIF kind routes governance verdicts to policy.* ([#166](https://github.com/trivoallan/knock/issues/166)) ([75eaf13](https://github.com/trivoallan/knock/commit/75eaf13cf32e98f02f0faac321b9d54b4c63f1c1))
* **image:** rebase runtime on Alpine + Python 3.13 ([#161](https://github.com/trivoallan/knock/issues/161)) ([494f1dc](https://github.com/trivoallan/knock/commit/494f1dcf055f6db80398778458b31d26426a12b0))


### Bug Fixes

* **adapters:** emit marked/attested/sbom counts in the journal summary ([#169](https://github.com/trivoallan/knock/issues/169)) ([d28135e](https://github.com/trivoallan/knock/commit/d28135e4454668e72bc7933c627ddad989069afd))
* **reconcile:** backfill SBOM coverage on kept digests ([#167](https://github.com/trivoallan/knock/issues/167)) ([29d12e6](https://github.com/trivoallan/knock/commit/29d12e672b5f9b99d2a2a10908570ff362dce9f3))


### Documentation

* **docs:** docs polish pass — architecture narrative, rebuild/harden how-to, slidev deck ([#155](https://github.com/trivoallan/knock/issues/155)) ([448329f](https://github.com/trivoallan/knock/commit/448329f1afa9f7fa9e338756276c9ad7b8793dd9))
* **docs:** documentation polish pass — architecture page, restructured reference, search, glossary, llms.txt ([#157](https://github.com/trivoallan/knock/issues/157)) ([1ded166](https://github.com/trivoallan/knock/commit/1ded166b80c0c6d9b8da1b515b8b3c5a7c252f7d))
* **docs:** publish the SARIF ingestion profile reference ([#168](https://github.com/trivoallan/knock/issues/168)) ([30ea1f2](https://github.com/trivoallan/knock/commit/30ea1f24100d202a14d4ef42a41eea694fe898d1))
* **how-to:** governance findings as portable provenance (any SARIF analyzer) ([#164](https://github.com/trivoallan/knock/issues/164)) ([e7ea143](https://github.com/trivoallan/knock/commit/e7ea143e7825bcfd8defc45ab3d7a0d742f1bd8c))
* **roadmap:** decouple forward direction from versions; re-theme Now to adoption ([#153](https://github.com/trivoallan/knock/issues/153)) ([9ff6a42](https://github.com/trivoallan/knock/commit/9ff6a42ac3ca2b38d9d0615a36e41ab8bfe18deb))
* **roadmap:** defer MirrorPolicy-as-CRD (operator unrequested) ([#159](https://github.com/trivoallan/knock/issues/159)) ([ccaa7ac](https://github.com/trivoallan/knock/commit/ccaa7ac678b27a3c54da038a1114f53dc200cb6f))
* **roadmap:** re-snapshot to 0.8.0 ([#171](https://github.com/trivoallan/knock/issues/171)) ([1d191b6](https://github.com/trivoallan/knock/commit/1d191b60c48309bd2c341a34efae3b797a9d11c8))

## [0.7.0](https://github.com/trivoallan/knock/compare/v0.6.0...v0.7.0) (2026-06-17)


### Features

* attach registry-config parity — wire KNOCK_REGISTRIES into `knock attach` ([#97](https://github.com/trivoallan/knock/issues/97)) ([c320179](https://github.com/trivoallan/knock/commit/c3201790552a85985e47daa215cbd4d001e0b737))
* **audit:** digest on every coverage outcome + observational --sbom tier ([#143](https://github.com/trivoallan/knock/issues/143)) ([522b764](https://github.com/trivoallan/knock/commit/522b764ce5251daf886cd488657e28b1e71b2173))
* **deploy:** default argocd-prod policy to the bundled busybox example ([014cb95](https://github.com/trivoallan/knock/commit/014cb95be45b8c69658b3c3ea2fd88c4012da62b))
* **deploy:** default overlays/prod policy to the busybox example ([#87](https://github.com/trivoallan/knock/issues/87)) ([208bd85](https://github.com/trivoallan/knock/commit/208bd85562c7382c3d7aec18eea48025963a43b7))
* **deploy:** Dependency-Track consumer + XZ (CVE-2024-3094) incident demo for package-level blast-radius ([#136](https://github.com/trivoallan/knock/issues/136)) ([b1b2034](https://github.com/trivoallan/knock/commit/b1b203464003db725445c0962b63e2f74b4b486c))
* **deploy:** make argocd-prod + argocd-seed (bring up the full prod App-of-Apps on kind) ([59b69f2](https://github.com/trivoallan/knock/commit/59b69f2eb1c2466dfc8c9e81715ffb594c0933d8))
* **deploy:** make argocd-prod a complete end-to-end mirror on kind ([76ac75b](https://github.com/trivoallan/knock/commit/76ac75be6b9618dcf5cb54db40728a1fa508949c))
* **deploy:** weekly knock-gc CronJob in the reference deployment ([#107](https://github.com/trivoallan/knock/issues/107)) ([a06d171](https://github.com/trivoallan/knock/commit/a06d171e102f77933730b9ad487e1f8b91267978))
* **deploy:** Zot demo registry with built-in UI; text logs in Jobs ([#92](https://github.com/trivoallan/knock/issues/92)) ([21dc7bd](https://github.com/trivoallan/knock/commit/21dc7bdf4ac1a1c5a2842142ac26fc65ac749163))
* **domain:** stamp OCI image.title and configurable vendor ([#134](https://github.com/trivoallan/knock/issues/134)) ([1253d70](https://github.com/trivoallan/knock/commit/1253d703902ae7a208f47c5b49d5d73492f209db))
* finding-type-aware SARIF mapper (rule evaluations ≠ vulnerabilities) ([#102](https://github.com/trivoallan/knock/issues/102)) ([3aee05b](https://github.com/trivoallan/knock/commit/3aee05be6e5d5ba9d537c6f62548904bbcfae14a))
* knock attach --fail-on &lt;severity&gt; CI gate ([#86](https://github.com/trivoallan/knock/issues/86)) ([f0cf0bc](https://github.com/trivoallan/knock/commit/f0cf0bcd2a7389a421789ce192d676eecf9e8275))
* multi-owner ownership (io.knock.owners) ([#100](https://github.com/trivoallan/knock/issues/100)) ([139f7b1](https://github.com/trivoallan/knock/commit/139f7b11f26fc3bff5ac717bac499970a08a50c2))
* SBOM generation on the rebuild path (package-level blast-radius) ([#128](https://github.com/trivoallan/knock/issues/128)) ([003f0b8](https://github.com/trivoallan/knock/commit/003f0b85bded244cc3621ea0da55d779dfd6f391))
* scan-referrer garbage collection (knock gc) ([#105](https://github.com/trivoallan/knock/issues/105)) ([b9892d6](https://github.com/trivoallan/knock/commit/b9892d626b24e867496332fe7f15dfcef0019991))
* sign the SBOM under knock's identity (trust tier) ([#144](https://github.com/trivoallan/knock/issues/144)) ([f2fec80](https://github.com/trivoallan/knock/commit/f2fec80ff2c1aa23ec3417e677aac46abc99ee96))
* signed-coverage audit tier (knock audit --signed) ([#98](https://github.com/trivoallan/knock/issues/98)) ([3e4336d](https://github.com/trivoallan/knock/commit/3e4336d4347704652821a2583f45fc3246c3fd07))
* unify SBOM generation on syft — both paths, SPDX + CycloneDX ([#140](https://github.com/trivoallan/knock/issues/140)) ([eec9aa2](https://github.com/trivoallan/knock/commit/eec9aa2caa07437fc7297dd2599d6051041f188a))


### Bug Fixes

* **.claude:** point superpowers at its own marketplace so it loads in ephemeral sessions ([#94](https://github.com/trivoallan/knock/issues/94)) ([578601d](https://github.com/trivoallan/knock/commit/578601d53a95fa8abf6b7239e1582c93363e73c2))
* **adapters:** honor tls_verify on the BuildKit push path ([#127](https://github.com/trivoallan/knock/issues/127)) ([118ec54](https://github.com/trivoallan/knock/commit/118ec548e226e53afc3323a974bf3cd206c05b74))
* **ci:** update the manifests job for the single Argo app set ([#91](https://github.com/trivoallan/knock/issues/91)) ([ee1cad9](https://github.com/trivoallan/knock/commit/ee1cad904b7d7c7c4c1790aa761750f042a2ac90))
* **cli:** render failed policy with only operation errors ([#126](https://github.com/trivoallan/knock/issues/126)) ([36efafd](https://github.com/trivoallan/knock/commit/36efafd6919e490e4ef8522786786441ee64c170))
* demo reference policy/rebuild — busybox tag selection + arch-robust inspect ([#139](https://github.com/trivoallan/knock/issues/139)) ([51e8bc1](https://github.com/trivoallan/knock/commit/51e8bc1fd6baab748cc85841efd8693485ed5b27))
* **deploy:** select the OpenBao server pod by name, not a chart label ([#88](https://github.com/trivoallan/knock/issues/88)) ([16d4c56](https://github.com/trivoallan/knock/commit/16d4c56c8f8d94e5aab605eee71b7ad7fe784348))
* **deps:** clear docs-site high/uuid CVEs — bump Docusaurus + pin transitives ([a5a9c20](https://github.com/trivoallan/knock/commit/a5a9c20074acdf45cb1a5b87a3c40cde3eb12047))
* **deps:** update react monorepo to v19 ([#138](https://github.com/trivoallan/knock/issues/138)) ([1d78def](https://github.com/trivoallan/knock/commit/1d78defd8c67a4615d27d20c361bc5098c59ef3a))


### Documentation

* add "Why knock" argument page ([#104](https://github.com/trivoallan/knock/issues/104)) ([0dbffb9](https://github.com/trivoallan/knock/commit/0dbffb97682c7fe06b1300b9ff28214e36bdc779))
* add the CLI command reference to the reference section ([#125](https://github.com/trivoallan/knock/issues/125)) ([786b412](https://github.com/trivoallan/knock/commit/786b412b775c1a6adac86ad8b483c39472892554))
* **docs:** mark Now items delivered; renumber signed-coverage ADR to 0026 ([#99](https://github.com/trivoallan/knock/issues/99)) ([ff5fdea](https://github.com/trivoallan/knock/commit/ff5fdea279d899e5856ed902c6f8401f026ae539))
* fix the generated reference's in-page TOC anchors ([#121](https://github.com/trivoallan/knock/issues/121)) ([3cf1ffb](https://github.com/trivoallan/knock/commit/3cf1ffbbb09345a4c427d57ab93defc30b6d12d8))
* generate how-to & explanation section indexes from page front-matter ([#147](https://github.com/trivoallan/knock/issues/147)) ([b88f452](https://github.com/trivoallan/knock/commit/b88f4526251bb93047f220e869aeb13074be05b0))
* generate policy + config reference from the Pydantic schemas ([#108](https://github.com/trivoallan/knock/issues/108)) ([f8e52b7](https://github.com/trivoallan/knock/commit/f8e52b7bd6234d1aa2d199d26909343974547180))
* generate policy + config reference from the Pydantic schemas ([#109](https://github.com/trivoallan/knock/issues/109)) ([28651c2](https://github.com/trivoallan/knock/commit/28651c28581774990b5e25ad80b28f0b08eefb1d))
* order the Reference sidebar — mirror-policy before config ([#120](https://github.com/trivoallan/knock/issues/120)) ([cb35be0](https://github.com/trivoallan/knock/commit/cb35be0977677dd193b6774dd32c46ac835977ea))
* order, label, and flatten the docs-site sidebar ([#118](https://github.com/trivoallan/knock/issues/118)) ([f7cb370](https://github.com/trivoallan/knock/commit/f7cb370188cc43c12f973549cf2ae3f058c42e0b))
* prepare 0.7.0 release — stamp roadmap + getting-started version ([2daebf9](https://github.com/trivoallan/knock/commit/2daebf9679ba2c04fe5e3499c9b8a9d4c19e3c1b))
* promote the local walkthrough to a top-level Getting started ([#106](https://github.com/trivoallan/knock/issues/106)) ([ba91bf9](https://github.com/trivoallan/knock/commit/ba91bf901dbdbd9c83a86666df8847683dd0c50b))
* publish the docs site with Docusaurus on GitHub Pages ([#115](https://github.com/trivoallan/knock/issues/115)) ([3e5eba3](https://github.com/trivoallan/knock/commit/3e5eba3d6c47ec4d357a8ac08fff6d34e86e8359))
* record the Dependency-Track boundary (attach = scan provenance, not a store) ([#135](https://github.com/trivoallan/knock/issues/135)) ([28af8a4](https://github.com/trivoallan/knock/commit/28af8a47f5099fab4d3a5a2f4d726356a4e7f5e8))
* refresh README for the delivered mandate, gc, and the docs site ([#110](https://github.com/trivoallan/knock/issues/110)) ([10a92cb](https://github.com/trivoallan/knock/commit/10a92cbc9b221cc71ff834d89caef739663c3dd6))
* renumber duplicate ADR 0026 → 0030 (multi-owner ownership) ([#122](https://github.com/trivoallan/knock/issues/122)) ([cab739c](https://github.com/trivoallan/knock/commit/cab739c4f607e9e199421cdbe6a589f1a7390801))
* restructure docs into Diátaxis sections (step 1 — folders + moves) ([#111](https://github.com/trivoallan/knock/issues/111)) ([1009b72](https://github.com/trivoallan/knock/commit/1009b72f1f64b18826724cc422b0eb0e3a099f01))
* restructure docs into Diátaxis sections (step 1) ([#112](https://github.com/trivoallan/knock/issues/112)) ([c0c52cd](https://github.com/trivoallan/knock/commit/c0c52cd004fd1b4c263a01eed073a01fa66e69c0))
* roadmap — add docs-site item, cut deferred bets, proxycache out of scope ([#103](https://github.com/trivoallan/knock/issues/103)) ([5685732](https://github.com/trivoallan/knock/commit/5685732c1c8cffe8cdb90efab252e4725efc399b))
* **roadmap:** sync Now/Next/Later — the mandate is delivered ([#90](https://github.com/trivoallan/knock/issues/90)) ([e3de9ad](https://github.com/trivoallan/knock/commit/e3de9ad769a381ad6f55350e9ea24ba4b181b68b))
* scan attestation max-age freshness contract (admission gate) ([#137](https://github.com/trivoallan/knock/issues/137)) ([5fc046b](https://github.com/trivoallan/knock/commit/5fc046bff8320fe318c0c68524dc0b7424f7f2f8))
* spec — Docusaurus docs site published to GitHub Pages ([#114](https://github.com/trivoallan/knock/issues/114)) ([335d459](https://github.com/trivoallan/knock/commit/335d459c7ebe96aa39e27874cdad38349b2cf250))
* split the examples mega-doc into how-to + explanation (step 2) ([#113](https://github.com/trivoallan/knock/issues/113)) ([fbf7db8](https://github.com/trivoallan/knock/commit/fbf7db8225d2038d5a2c7db1a2296ae4cec7c5ad))
* sync getting-started + architecture overview with shipped state ([#101](https://github.com/trivoallan/knock/issues/101)) ([04f0669](https://github.com/trivoallan/knock/commit/04f0669b02c248b2e85c0c5962ea173b3383fdd9))
* sync README/roadmap/design with shipped SBOM, gc, and docs site ([#146](https://github.com/trivoallan/knock/issues/146)) ([8e3fc88](https://github.com/trivoallan/knock/commit/8e3fc88cfc271e1083e912450d6aa9c0a2c727f6))

## [0.6.0](https://github.com/trivoallan/knock/compare/v0.5.0...v0.6.0) (2026-06-15)


### Features

* complete attestation coverage — sign copy path + idempotent skip-backfill ([#77](https://github.com/trivoallan/knock/issues/77)) ([9a3515a](https://github.com/trivoallan/knock/commit/9a3515aaf0ac645577d85af95cfbf53d7b874144))
* **deploy:** ArgoCD App-of-Apps reference deployment (variant) ([#79](https://github.com/trivoallan/knock/issues/79)) ([98f2669](https://github.com/trivoallan/knock/commit/98f26698b8498c5f6f3a745dbf98c3bcb5ea7257))
* freeze org.opencontainers.image.revision semantics ([#80](https://github.com/trivoallan/knock/issues/80)) ([dad2ffc](https://github.com/trivoallan/knock/commit/dad2ffc22642fa1500f779aee9eafa70ec9ff0f3))


### Documentation

* **roadmap:** mark single-front-door mandate validated ([#76](https://github.com/trivoallan/knock/issues/76)) ([5d27d17](https://github.com/trivoallan/knock/commit/5d27d17254d942a92f2699e466856baeffc50c70))
* **roadmap:** restructure to Now/Next/Later ([#74](https://github.com/trivoallan/knock/issues/74)) ([bb7c172](https://github.com/trivoallan/knock/commit/bb7c172a5338c6d3e89c118bd46a2cb8ee4f5a23))

## [0.5.0](https://github.com/trivoallan/knock/compare/v0.4.0...v0.5.0) (2026-06-15)


### Features

* **deploy:** buildkitd autoscaling — KEDA scale-up 1→K (impl of ADR 0016) ([#59](https://github.com/trivoallan/knock/issues/59)) ([baaca35](https://github.com/trivoallan/knock/commit/baaca354ee6d573be9caca0ef3c56f5c3e53aa0e))
* **domain:** deb822 support in rewritePackageSources ([#61](https://github.com/trivoallan/knock/issues/61)) ([b8efe9f](https://github.com/trivoallan/knock/commit/b8efe9faf98c64e1fdc886508bb468c2f46debee))
* **reconcile:** retention-driven soft-delete (roadmap item 5) ([#63](https://github.com/trivoallan/knock/issues/63)) ([3fdbf95](https://github.com/trivoallan/knock/commit/3fdbf95860d68add79ca8c11bb4e74db1cbb9a2e))
* scan attestation — sign the knock attach referrer ([#56](https://github.com/trivoallan/knock/issues/56)) ([388e970](https://github.com/trivoallan/knock/commit/388e9701bd003e97db5b847856d1598b72871f7d))


### Bug Fixes

* **adapters:** cosign v3 signing-config migration (signing was broken on bundled cosign) ([#58](https://github.com/trivoallan/knock/issues/58)) ([4af77b4](https://github.com/trivoallan/knock/commit/4af77b45bb9c5fab93a0a166b335f5572cdea114))
* **use-case:** scope reconcile unmark cleanup to the axis being unmarked ([#64](https://github.com/trivoallan/knock/issues/64)) ([ff3a957](https://github.com/trivoallan/knock/commit/ff3a957db13f411b3705341250b766b973a11e22))


### Documentation

* **readme:** bring README + examples current with v0.5 ([#65](https://github.com/trivoallan/knock/issues/65)) ([4808315](https://github.com/trivoallan/knock/commit/4808315487f3397fd008c296740f6abaee0c2c24))
* **readme:** bring README current with v0.4 (commands, SLSA, autoscaling, arch) ([#60](https://github.com/trivoallan/knock/issues/60)) ([00d8946](https://github.com/trivoallan/knock/commit/00d89462b1b82d81e49cb764f6db7a9895336e11))
* **roadmap:** mark lifecycle ⑤ delivered and archive_restore rejected ([#66](https://github.com/trivoallan/knock/issues/66)) ([8bfd778](https://github.com/trivoallan/knock/commit/8bfd7786e17a88fbbea5f0fab48096986cdb194c))
* **specs:** buildkitd autoscaling design — KEDA-driven scale-up ([#43](https://github.com/trivoallan/knock/issues/43)) ([3046148](https://github.com/trivoallan/knock/commit/3046148b175f5ded87086082aad08a1bb1e0c529))

## [0.4.0](https://github.com/trivoallan/knock/compare/v0.3.0...v0.4.0) (2026-06-13)


### Features

* concurrent reconcile (scale-up) + horizontal sharding (scale-out) ([#37](https://github.com/trivoallan/knock/issues/37)) ([6cb2d29](https://github.com/trivoallan/knock/commit/6cb2d297996548a3b346739e2e36782d2552e1d2))
* coverage audit — knock audit (roadmap ④) ([#53](https://github.com/trivoallan/knock/issues/53)) ([248dd1a](https://github.com/trivoallan/knock/commit/248dd1a1cb567b4953489a07ceed4f667b6e0ba4))
* delegated tag deletion (soft-delete via OCI referrer, mode cascade) ([#41](https://github.com/trivoallan/knock/issues/41)) ([d8a0e43](https://github.com/trivoallan/knock/commit/d8a0e43ed3bef77faa5a506f06dc738e94257c9f))
* **deploy:** local-transform demo tier — self-contained rebuild path (no Harbor) ([#32](https://github.com/trivoallan/knock/issues/32)) ([a8954f5](https://github.com/trivoallan/knock/commit/a8954f57d47846d1759e5d3509cb98d8f13be68d))
* **deploy:** opt-in source-registry credentials to dodge Docker Hub rate limits ([#34](https://github.com/trivoallan/knock/issues/34)) ([a271fa1](https://github.com/trivoallan/knock/commit/a271fa15c9e337a0636554f3f8fa5bf58a6b832c))
* knock attach — ingest + stamp scan results as OCI referrers ([#42](https://github.com/trivoallan/knock/issues/42)) ([b2afcb6](https://github.com/trivoallan/knock/commit/b2afcb6c7e718a9336496efc9009b8c34c8f558d))
* knock purge — the reference reaper (usage-gated tag deletion) ([#45](https://github.com/trivoallan/knock/issues/45)) ([f52eaee](https://github.com/trivoallan/knock/commit/f52eaee3d65d18c449afb1db967b094a00714b06))
* **reconcile:** surface applied transform steps + produced digest per operation ([#38](https://github.com/trivoallan/knock/issues/38)) ([602247a](https://github.com/trivoallan/knock/commit/602247a31dacc8a4a97f268d51f194b6db8a8fce))
* SLSA / in-toto attestation — sign the rebuild path (roadmap ①) ([#49](https://github.com/trivoallan/knock/issues/49)) ([2c1b6e4](https://github.com/trivoallan/knock/commit/2c1b6e4d8e3feb3ac6666052b10080bd5a3b4f5c))


### Documentation

* align CLAUDE.md with the delivered hexagon + embed design/specs in the C4 workspace ([#35](https://github.com/trivoallan/knock/issues/35)) ([42c10af](https://github.com/trivoallan/knock/commit/42c10afa4234257a647b111f473fe5afe4a6b53e))
* **architecture:** add `attach` to the C4 model + finish the CLAUDE.md inventory ([#55](https://github.com/trivoallan/knock/issues/55)) ([0d3a634](https://github.com/trivoallan/knock/commit/0d3a6344b12cf0cd7be81229d45397e6f04eefd3))
* **architecture:** add ADRs for the 3 remaining specs (scale-up/-out, source creds) ([#40](https://github.com/trivoallan/knock/issues/40)) ([c06bde0](https://github.com/trivoallan/knock/commit/c06bde0db5fb9e30110540d1edabbba7f468864c))
* **architecture:** add C4 container/component/hexagon views, refresh stale docs ([#39](https://github.com/trivoallan/knock/issues/39)) ([9525fa6](https://github.com/trivoallan/knock/commit/9525fa6346bd7845d43ddde3e189ee1883a7a7b9))
* **architecture:** link each view / example name to its Mermaid export ([#46](https://github.com/trivoallan/knock/issues/46)) ([769c86f](https://github.com/trivoallan/knock/commit/769c86fb56e67550d694bab9051a7245d88cfb90))
* **architecture:** redraw the error hierarchy as Mermaid in design.md ([#48](https://github.com/trivoallan/knock/issues/48)) ([41ed446](https://github.com/trivoallan/knock/commit/41ed44652b524b5b83ecac40276e3fd7505648b2))
* **architecture:** redraw the hexagon schema as Mermaid in design.md ([#47](https://github.com/trivoallan/knock/issues/47)) ([d26648c](https://github.com/trivoallan/knock/commit/d26648c1812467bb13800b1566e10082c47d0d1b))
* **architecture:** split the deployment view into one per example + commit Mermaid exports ([#44](https://github.com/trivoallan/knock/issues/44)) ([91c4f8d](https://github.com/trivoallan/knock/commit/91c4f8d2f008d2cc413948d8ac8209a1fdc91343))
* sync design.md + roadmap with delivered work (CLI verbs, SLSA, new ports/adapters) ([#54](https://github.com/trivoallan/knock/issues/54)) ([4153b9e](https://github.com/trivoallan/knock/commit/4153b9e349477d2033efbe755c1ef8d51656f3c6))

## [0.3.0](https://github.com/trivoallan/knock/compare/v0.2.0...v0.3.0) (2026-06-12)


### Features

* **deploy:** reference deployment — kustomize, kind demo, blast-radius consumer ([#29](https://github.com/trivoallan/knock/issues/29)) ([68d7535](https://github.com/trivoallan/knock/commit/68d75352c9335807a5267a10f7284d733fbf7a17))
* **domain:** pluggable transform-step registry (+ setTimezone, discriminated-union schema) ([#26](https://github.com/trivoallan/knock/issues/26)) ([3aad9dd](https://github.com/trivoallan/knock/commit/3aad9dddb0bf198163feb39dbc280b658c4b12f9))
* image transform / hardening — the rebuild path (Phase 6) ([#24](https://github.com/trivoallan/knock/issues/24)) ([79e9dfa](https://github.com/trivoallan/knock/commit/79e9dfaba51a91e30f58af05848637b81bf54c44))
* make the reconcile path runnable — bundle regctl + per-registry TLS/CA ([#27](https://github.com/trivoallan/knock/issues/27)) ([de46501](https://github.com/trivoallan/knock/commit/de465015ddc6369d48238fcd0d322544dcfee424))
* **reconcile:** structured two-stream output + per-policy resilience ([#25](https://github.com/trivoallan/knock/issues/25)) ([1aa22f2](https://github.com/trivoallan/knock/commit/1aa22f24ca9a18a56ae2ca3c0602781faf327a3d))


### Bug Fixes

* **image:** run runtime image as non-root (uid 65532) ([#30](https://github.com/trivoallan/knock/issues/30)) ([409b0fe](https://github.com/trivoallan/knock/commit/409b0febf5d08c29a496190e518df6a6bd54880a))


### Documentation

* **architecture:** VS Code launch config for Structurizr C4 viewer ([#22](https://github.com/trivoallan/knock/issues/22)) ([53e2ad3](https://github.com/trivoallan/knock/commit/53e2ad362b2dbf0c7b5e97aa245561a91e0ad1af))
* **specs:** SLSA / in-toto attestation design — the heavy-provenance layer ([#28](https://github.com/trivoallan/knock/issues/28)) ([5f36c15](https://github.com/trivoallan/knock/commit/5f36c151d2bf0ee5e51952f1728410ff9ca59483))

## 0.2.0

Initial public release.

`knock reconcile <dir>` — declarative **MirrorPolicy** reconciliation for external OCI
artifacts: regex/semver/name tag selection, derived moving-tag aliases, per-variant
expansion, provenance-based change detection, and OCI provenance stamping applied over
`regctl` (copy path). Multi-registry env-roster config; structured run summary.
