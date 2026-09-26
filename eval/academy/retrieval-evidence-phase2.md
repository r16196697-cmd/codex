# Bootstrap Academy Phase 2 — Search / Evidence + Memory Retrieval

## Status and scope

This is a deterministic synthetic qualification of the audited v0.1 Core, not a model-quality experiment. It tests the current Memory/Search and Verification paths without creating a MODEL Run. Results are Academy observations only; no Foundation Contract, Core semantics, production policy, or ranking policy changed.

The Host experiment settings remained frozen: primary ChatGPT/Codex Memory is OFF; “allow memory from tool chats” is `ON_FORCED / USER_NOT_CONTROLLABLE`; whether that control has cross-session effect while primary Memory is OFF is `NOT INDEPENDENTLY VERIFIED`. Custom Instructions and global AGENTS remain unchanged. `project-experience-curator` remains `DISCOVERED / UNEVALUATED`. This is a potential contamination variable for a future real-model behavioral A/B, not Nexus retrieval or exposure evidence.

Phase 2 answers no question about whether a model saw or benefited from a proposed pack. `PROPOSED_GROUNDING_PACK_NOT_EXPOSED` is an Academy-only record. `T1 PASS` means stored object integrity only. Synthetic T3 approvals and declared independence groups exercise deterministic policy/API paths; they do not establish real-world source truth or source independence.

## Audited implementation surface observed

The experiment used the existing `MemoryService.search_raw` and `search_admitted` APIs, existing task Run data boundaries and Grant resource scopes, object classification, `VerificationService` T1 and human/domain T3 records, Memory candidate admission/quarantine, command replay, Trace, and `CodexHostedBridge.record_evidence` for one actual public Search receipt.

No Core API exposes a diagnostic reason for every excluded search candidate, a freshness-aware ranker, a source-independence evaluator, a semantic truth ranker, a context/grounding-pack object, or model-exposure proof. Those remain `NOT IMPLEMENTED / CANDIDATE` or `ACADEMY GAP`. Purge-barrier exclusion was not expanded into a new purge workflow; it is deferred to the failure-recovery course. The fixture does verify ordinary authority, classification, expiry, quarantine, and the current search path’s barrier filter where exercised by the existing implementation.

## Frozen fixture and method

Before running the runner, the fixed fixture `retrieval-evidence-phase2-fixture.json` was frozen and hashed in the machine result. It contains 18 unique synthetic, non-production items, 14 fixed query/ground-truth pairs, and 9 parser probes. Although all payloads are synthetic, classification labels are deliberately mixed test variables and are not all PUBLIC (including a PERSONAL boundary probe). Ground truth is authored in the fixture, separate from search output. The runner never uses expected IDs to add a result or pack entry; a search miss stays a miss.

Items cover clear facts, multi-fact composition, alias wording, same-topic distractors, irrelevant content, stale/superseded facts, a newer fact, conflicting claims, T3-admitted claims, T1-only quarantined claims, an expired candidate, a PERSONAL-classified claim, an authority-inaccessible claim, missing evidence/provenance, and instruction-like untrusted source text. Object IDs and receipt payloads are unique. Distinct source groups are explicitly declared in the synthetic fixture rather than inferred from identical text.

The runner bootstraps two synthetic Tasks/Runs using the Hosted Bridge and public Core APIs. A writer Run creates the corpus; a separate PUBLIC-only reader Run performs fixed queries. The result records raw discovered IDs, admitted IDs, current-Run eligible IDs, and proposed pack refs separately. “Eligible” here is the result of the existing admitted search under the reader Run’s actual Grant and classification boundary, not a new selector.

The runner uses the repository default fail-closed policy as its base. Its disposable in-memory policy changes only `trust_anchors` to the synthetic `academy-human-root`; no other policy field is changed, and the persistent default policy file is untouched.

For each query, precision@k is relevant hits in the first `min(k, returned_count)` divided by k; recall@k is hits in the first k divided by the fixture’s relevant count (null for no relevant items); MRR is reciprocal rank of the first relevant item, or zero if absent. Aggregate values are an unweighted macro-average over fixed queries with non-null metric values. These are deterministic retrieval metrics, not model quality.

Q0 passes each raw probe to the current API. Q1 is a separately labeled Academy-only normalization candidate that replaces punctuation with spaces. Core query semantics were not changed. R1/R2 are selected only from actual `search_admitted` results: R1 takes the first result from the frozen broad-retention query; R2 unions that query with the fixed stale-window query and deduplicates by returned Object ID.

## Deterministic retrieval result

The corpus produced 13 ADMITTED and 4 QUARANTINED Memory candidates; the remaining item had insufficient evidence and no candidate was created. The fixed queries yielded these macro retrieval metrics (the aggregate values happened to be the same for raw and admitted search in this query set):

| Set | Precision@1 | Precision@3 | Precision@5 | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `search_raw` | 0.3571 | 0.1667 | 0.1000 | 0.5417 | 0.6250 | 0.6250 | 0.3571 |
| `search_admitted` | 0.3571 | 0.1667 | 0.1000 | 0.5417 | 0.6250 | 0.6250 | 0.3571 |

Equal aggregate scores do not mean equal result sets: for the quarantined amber query, raw search returned the quarantined candidate while admitted search returned none. Broad retention raw search also included quarantined conflict material that admitted search excluded. The query-level result JSON retains IDs, ranking order, fixture relevance labels, and metrics, so the aggregate cannot hide this distinction.

The current lexical FTS path missed the fixed multi-term formulations `q-current-retention`, `q-composition`, and `q-independent-sources` (no expected-ID fallback was used). Exact lexical/alias queries such as `q-alias-fortnight`, `q-route`, and `q-stale-window` returned their seeded claims. This is evidence about this small fixed corpus and query set only.

Eligibility boundary probes confirmed that the expired item was absent from raw/admitted results, the PERSONAL item was excluded by the PUBLIC reader boundary, the authority-inaccessible item was absent from its Grant scope, and quarantined candidates did not enter `search_admitted`. `q-quarantined-amber` did surface its candidate in raw search, demonstrating `raw discovered` is not equivalent to grounded/eligible Memory.

## Query robustness

Nine fixed probes covered hyphen, quote, apostrophe, slash, colon, parentheses, multiword phrase, Chinese Unicode, and mixed case. Current Q0 returned explicit FTS errors for hyphen, apostrophe, slash, colon, and parentheses. Academy-only Q1 normalization made those probes execute successfully; quote/phrase/Unicode/case probes executed under both. This forms a `Query Normalization Candidate` for further relevance/error analysis, not a production change or a claim that normalized results are more correct.

## Evidence and truth boundaries

- Two synthetic declared independent source groups plus a synthetic human/domain attestation exercised T3 admission for selected claims. This is API-path qualification, not external corroboration.
- A stale/superseded historical claim and a fresh primary claim were both retrievable when their terms matched. The search API did not impose a freshness ranking; freshness-aware policy remains unfrozen.
- The contradictory synthetic 12-day claim remained `QUARANTINED` with `truth_state=UNKNOWN`; both supporting and contradicting source relations are recorded in the fixture.
- The insufficient-evidence claim returned `INCONCLUSIVE`; no Memory candidate was created.
- A T1 integrity check returned `PASS` for the old amber claim’s bytes, while its candidate remained quarantined. Integrity did not become semantic support.
- Instruction-like text remained untrusted evidence; its candidate was quarantined and the writer Grant’s authority scope was unchanged.
- A missing-source-provenance claim stayed quarantined.

One actual read-only Host Search observation, query `SQLite FTS5 MATCH query syntax`, was supplied to the runner and recorded through `CodexHostedBridge.record_evidence`. The committed runner does not execute network Search: the query, URL, and excerpt were caller-observed Host output. The source URL and excerpt are retained only in the disposable external Academy database, not this repository. The sanitized result records `observation_source=CALLER_OBSERVED_HOST_SEARCH`, `network_search_executed_by_runner=false`, `receipt_persisted_by_runner=true`, and `reproducibility=HOST_OBSERVATION_NOT_REPERFORMED_BY_COMMITTED_HARNESS`, plus host `www.sqlite.org`, Evidence ID, T1 integrity verdict, Trace event ID, and `CODEX_HOST_DECLARED`; provider/model/request identity is unavailable. That T1 PASS checks receipt bytes only, not documentation semantic truth. Conflict, insufficient evidence, and injection-like behavior were qualified with fixed synthetic cases; no live hostile-content URL was visited.

## Proposed pack measurements

All counts below are Object refs (claim plus Evidence refs); serialized size is canonical JSON bytes/chars. The pack records were not exposed to a model. Host token/cost telemetry is `UNAVAILABLE`; no token estimate is substituted.

| Condition | Objects | Bytes / chars | Sources / declared independent groups | Stale items | Conflict items |
| --- | ---: | ---: | ---: | ---: | ---: |
| R0 NONE | 0 | 2 / 2 | 0 / 0 | 0 | 0 |
| R1 TOP-K minimal | 3 | 490 / 490 | 2 / 2 | 0 | 0 |
| R2 broader eligible | 12 | 2,056 / 2,056 | 8 / 8 | 1 | 0 |

R2 adds three claim records beyond R1, including the stale superseded item and a multi-fact composition claim; it is larger and contains a stale item. No quarantined/conflicted content entered either proposed pack. These are exposure-size and inclusion measurements only: no model accuracy, distraction, token savings, success uplift, or behavioral effect was measured.

## Replay, persistence, and live receipt

An exact committed raw-retain command replay left Object, Run, budget reservation, Trace, raw/admitted Memory, Verification, candidate, and CommandLedger counts unchanged. Reusing that command ID with a changed Object ID produced `COMMAND_CONFLICT`. The synthetic root Runs were terminalized as SUCCEEDED; the persisted API fixture produced zero MODEL Runs and no MODEL execution receipt.

## Gaps and candidates

- `Memory Retrieval Strategy Candidate`: investigate fixed multi-term misses and raw/admitted result-set differences. No selector/ranker change is justified by this sample alone.
- `Query Normalization Candidate`: Q1 avoids several observed FTS parser errors; relevance quality remains to be measured before any policy use.
- `Evidence Pack Candidate`: compare small/broad eligible packs in a future controlled behavioral study. Current structural sizes are not model exposure.
- `Source/Freshness Policy Candidate`: record freshness/provenance and evaluate it; no primary-always-wins policy is adopted.
- `Conflict Handling Candidate`: preserve current UNKNOWN/quarantine distinction; no automatic conflict resolution is proposed.

No embedding/vector index, reranker, model invocation, external paid API, external write, or automatic Memory promotion was used. Host-native Memory behavior remains unknown and is not counted as Nexus retrieval, exposure, or success. Phase 2 remains `IN PROGRESS`; Production qualification remains `NOT STARTED`.
