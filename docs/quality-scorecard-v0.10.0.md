# ReleaseReviewer 0.10.0 blind-review scorecard

Codex Code Review guidance is checked on four axes: **coverage**, **restraint**, **retention** of ordinary bug finding, and **actionability**. The evaluation includes intended violations, safe counterexamples, and unrelated changes; repository guidance should be scoped and show the safe path. See [OpenAI's Code Review rules evaluation](https://developers.openai.com/blog/custom-code-review-rules-for-codex).

## Per-report scores

| Axis | Pass condition |
| --- | --- |
| Coverage | Every oracle-required direct P0/P1 is found with the correct final behavior, triggering path, impact, and fixed-source evidence; every required dependency/checklist facet and supporting-Repo contract is covered; required gaps remain visible. |
| Restraint | Safe controls do not receive a P0/P1 or invented dependency; unused settings and equivalent moves are not misclassified; unknown providers are not guessed; a new resource alone is not treated as a code blocker. |
| Retention | Ordinary serious defects outside the new resource rules remain detectable, including startup data loss and consumer-facing contract breaks. |
| Actionability | Every P0/P1 identifies changed lines, trigger, final erroneous behavior, release impact, minimal fixed path/line citation, and a concrete correction. Every checklist item names a target, service identity, operation, and supporting evidence. |

Each axis is `pass`, `fail`, or `not-applicable`, with a short rationale and cited report lines. Do not score by issue count or checklist granularity. A sample passes only when every applicable axis passes and the integrity gates below pass.

## Fixed case oracle

| Case | Required result |
| --- | --- |
| `p0` | Find startup's unconditional orders deletion as a direct P0; retain the separate DELETE permission check. |
| `p1` | Find `checkout()` failing because the producer removes `amount`; classify P1 and trace the invocation. |
| `safe` | No introduced blocker or invented external dependency for a compatible additive alias. |
| `net-zero` | Respect the final no-difference tree; do not revive a historical finding or merge-parent obligation. |
| `gap` | Preserve the known missing required consumer as an incomplete static gap. |
| `unused-setting` | Do not infer first use or a deployment dependency from an unused definition. |
| `provider-unknown` | Do not infer Kafka from the abstract bus; preserve only evidenced contract obligations. |
| `secrets` | No synthetic password or private-key canary appears in durable reports or retained logs. |
| `resources` | Checklist the new `BillingDb.dbo.InvoiceTax` migration, schema/order/compatibility and INSERT permission; FTP host/port 21/passive data-channel firewall/account/directory; SFTP host/port 22/account/key/host key/directory. Keep each item pending and do not raise a blocker from resource novelty alone. |
| `same-target` | Treat a changed SFTP endpoint and directory under the same key as a new target; retain pending confirmation and do not invent credentials. |
| `permission` | Add least-privilege DELETE permission for the existing Orders table and service identity without inventing a code blocker. |
| `group-contract` | Report the entry Repo's `amount`→`total` contract break against the pinned supporting Repo consumer; cite both Repo identities and do not guess infrastructure. |

## Blind smoke readout

The four-case matrix and resource/safe follow-ups below are diagnostic spot checks on earlier candidate payloads, not scored independent blind repetitions. The helper-benchmarked final candidate payload is `5ffca53b34c2d6e9dc71cee2ee9f0c6dd45cefa342be2ddb8bc3cb8beb7bfd00`. A final-payload Table/FTP/SFTP smoke completed with a report hash that matches its exact saved UTF-8 bytes. It remains unscored because the oracle was visible to the validation harness and global skill isolation is unproven. No row below is a semantic acceptance pass until a second reviewer compares the report with the oracle and scores each applicable axis.

| Case / payload | Observed report result | Score status |
| --- | --- | --- |
| `permission` / earlier payload | Complete; 0 blockers, 0 gaps, 5 pending checks; separate least-privilege DELETE permission for the existing Orders table. | Spot-check aligned; unscored |
| `same-target` / earlier payload | Complete; 0 blockers, 0 gaps, 5 pending checks; changed SFTP host and directory are treated as a new target without invented credentials. | Spot-check aligned; unscored |
| `group-contract` / earlier payload | Complete; one P1 identifies the entry producer's `amount`→`total` change against the pinned consumer; no Kafka inference. | Spot-check aligned; unscored |
| `group-contract` / final payload `5ffca53b…b7bfd00` | Complete; one P1 cites the entry producer and pinned `billing-consumer`, preserves the contract failure and avoids provider guesses; 0 gaps, 3 pending checks, zero fetches, cleanup verified, no secret leak. Runtime 311.05 s; report SHA-256 `3cc3117994626ed31dc41161a6e8dbcbe45673fd5e7fd9f2ee8307cea817546e` matches saved bytes. [Matrix and report](benchmarks/v0.10.0-quality-group-contract-final/quality-matrix.json) | Spot-check aligned; semantic score null |
| `resources` / repaired earlier fixture | Complete; 0 blockers, 0 gaps, 17 pending checks across the new Table/migration, passive FTP, and SFTP identities and operations. | Spot-check aligned; unscored |
| `resources` / first fixture | The helper correctly reported a P1 because the fixture had removed the existing `checkout()` caller. The fixture was invalid for the resource-only oracle and this sample is excluded. | Invalid fixture; excluded |
| `safe` / first pre-fix-payload attempt | No report; the helper terminated during Windows artifact-path handling before it could score the compatible alias. Cleanup was verified. | Runtime diagnostic; no semantic score |
| `safe` / earlier pre-fix payload `d9331237…000d8b4` | Complete; 0 blockers, pending checks, and gaps; no invented dependency; local fetch count 0, cleanup verified, and no secret leak. Runtime 233.66 s. | Helper integrity pass; semantic score null |
| `resources` / final payload `5ffca53b…b7bfd00` | Complete; 0 blockers, 20 pending checks, 0 gaps. Checklist covers BillingDb InvoiceTax schema/migration/INSERT, FTP `ftp.tax.example.invalid:21` in passive mode/data-channel firewall/account/directory, and SFTP `sftp.tax.example.invalid:22`/account/key/host-key/directory. Local fetch count 0, cleanup verified, no secret leak; runtime 242.56 s; report SHA-256 `e6ffcebaaf74613caa71401016734acee44d8a6110dd941ef2f657631c4c0820` matches saved bytes. [Matrix and report](benchmarks/v0.10.0-quality-resources-endpoint-check/quality-matrix.json) | Helper integrity pass; semantic score null |
| `p0` / older payload | Expected startup data-loss P0 and separate DELETE permission checklist; candidate payload hash differs from the current release candidate. | Historical diagnostic; excluded |

Raw reports, logs, matrix metadata, and the repaired resource and safe follow-ups are retained under [`docs/benchmarks`](benchmarks/). The failed safe-control job now records its helper error and cleanup outcome in separate fields.

## Integrity gates

- Reviewer receives only the source workspace, task, and selected skill; the oracle stays outside that workspace.
- Preserve the report hash, model/effort, condition, case, run number, elapsed seconds, exit code, release outcome, cleanup result, fetch counters and helper metrics. Unknown token/HTTP/SSH metrics stay `null`.
- Candidate report contains no synthetic secret sentinel.
- A true missing consumer, source, merge parent or mapping is never upgraded to complete.
- A no-skill cell is comparable only if user `CODEX_HOME` skill isolation is proven. Current runner records isolation as unproven, so those cells cannot support acceptance until isolated.
- Semantic scoring remains incomplete while any required run is missing or any sample is unscored. A successful CLI exit or published report is not a semantic pass.
