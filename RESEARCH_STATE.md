# Research State

Mutable handoff for active research. This file is not an audit result and must not override AGENTS.md or RESEARCH_RULES.md.

## How to use

- Update only when active research lineage/status materially changes.
- Record exact branch/commit, workflow/run, canonical dataset, current verification state, blocker, and next step.
- Do not copy transient state into AGENTS.md.
- New agents should verify GitHub state rather than trusting stale entries here.

## Active: PD 4H Binance Universe

- Purpose: direction-only Premium/Discount continuation event study across the Binance USD-M 15m 5Y universe, resampled to complete 4H candles.
- Branch: `research/pd-4h-binance-universe`
- Research script: `research/pd_direction_4h_universe.py`
- Workflow: `.github/workflows/pd-4h-binance-universe.yml`
- Canonical source collection run: `36095439671` (8 Binance data shards; migrate to versioned Release when archive is verified).
- Latest known successful pre-self-verifying run: `36124942371`, head `a93355370c245e54e386bb8b36d420cb2cac5947`.
- Self-verifying workflow introduced at commit `783b0fbea30b52f0c0bf96cc80270f6e529fcb9b`; trigger commit `aa17c33a61d4ca73700dab9461bac5ade5f61a50`.
- Acceptance: preflight pass; all 8 shards pass; inputs nonempty; eligibility/sanity pass; all 8 shard artifacts present; aggregate has no duplicate parameter rows and expected universe; final `pd-4h-direction-VERIFIED` artifact exists.
- Status: verification pending for the self-verifying lineage; do not quote aggregate research conclusions until that lineage is verified.
- Next step: inspect the run created from trigger `aa17c33a...`; if failed, follow AGENTS.md repair loop using new evidence; if passed, inspect VERIFIED artifact contents and update this state.

## Active: Binance 5Y permanent archive

- Source Actions run: `36095439671`.
- Intended immutable Release tag: `binance-um-15m-5y-v1`.
- Archive workflow branch: `infra/binance-5y-release-archive`.
- Last known archive run: `36123487610`; completion/assets must be verified before declaring the Release canonical.
- Next step: verify run conclusion, Release existence, expected dataset/manifests, and SHA256SUMS before updating AGENTS.md wording from conditional to confirmed canonical storage.
