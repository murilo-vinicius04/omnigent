# Muse Code value audit — 22 October 2026

Recommendation: **try High Usage ($15/month)** for the existing Spark workload. It provides 5× Everyday allowance for 3× price, or 67% more allowance per dollar (40% lower capacity cost per comparable task). This is a capacity recommendation; no controlled quality benchmark establishes that Muse replaces Claude or Codex. No subscription, runtime configuration, or accounting code was changed during this audit.

## Work found

Read the two Omnigent conversations in the read-only SQLite store and native Muse logs, including worker and automated approval records. A separate initial native CLI session also worked on Omnigent integration.

| Native session prefix | Work in history | Input including cache | Output |
| --- | --- | ---: | ---: |
| `01a16110` | Provider visibility, model/effort selection, contributor setting and permissions debugging | 77.11M | 296k |
| `01a1616a` | First quota-ring implementation; initially unsupported/frozen | 24.95M | 89k |
| `01a16356` | Quota refresh, eventual free metadata read, usage plumbing and debugging | 118.60M | 277k |
| `01a163ae` | Hub lane: quota investigation, lightweight catalog check, token reader and monitoring wiring | 30.90M | 115k |
| `01a163e1` | Same Hub conversation after restart: form elicitation support, monitoring/comparison | 8.04M | 35k |

These rows include each root's workers and approvals; small probe sessions account for the rest. They are workstreams, not five independently validated completed features. History reports tests passing, but this audit did not rerun implementation tests. Both the quota ring and usage reader required iterations; Muse initially asserted no free quota endpoint and no raw token source, then found both after user challenges. Some changes initially landed in the wrong worktree before being ported to the live one. The reported 16-test catalog benchmark was not a matched rerun of the whole earlier Codex investigation.

## Total recorded use

Native activity: **2026-10-22 00:44:36–14:02:42 UTC** (previous evening 21:44 through 11:02 in São Paulo). 124 session logs with usage, including workers; **1,400 usage events** = 1,176 model completions + 224 automated approval reviews. All report Spark 1.3, including nested model IDs on approval records.

- Input: **259,823,942**, including **247,098,891 cached (95.10%)**.
- Fresh input: **12,725,051**; output **816,962**.
- Reasoning **503,272**, reported separately; do not add it again to output.
- Last meter: Everyday, **99% five-hour / 61% weekly**, as of 14:08:55 UTC. Five-hour reset 16:19:37 UTC (13:19:37 São Paulo); weekly reset 26 October 00:00 UTC.

This is repeated processed context, not 260M distinct useful tokens. Do not divide the complete lifetime total by 61: there is no clean initial weekly reading, and several windows/workstreams overlap.

## Quota efficiency

Calibration **12:10:32–14:06:41 UTC**, after contradictory earlier samples:

| Provider | Observed tokens: input + output | Five-hour change | Weekly change | Tokens / five-hour point | Tokens / weekly point |
| --- | ---: | ---: | ---: | ---: | ---: |
| Muse Everyday | 80.787M | 58→99 (+41) | 47→61 (+14) | 1.970M | 5.770M |
| Codex Plus, mixed Sol/Luna and reviews | 5.652M | 32→55 (+23) | 46→50 (+4) | 0.246M | 1.413M |
| Muse High, proportional projection | same work | ~8.2 points | ~2.8 points | ~9.852M | ~28.852M |

Codex figures are independently rebuilt from native rollouts (74 unique cumulative usage changes), not trusted blindly from Hub model_call rows. Same-window Hub rows only contain 5.016M tokens, and their model mix differs. Earlier window **11:35–12:53:15 UTC**: native Codex 14.819M tokens, +46 five-hour/+7 weekly = 0.322M/2.117M per point. Thus Muse's observed advantage spans ~6–8× processed tokens per five-hour point and ~2.7–4.1× per weekly point across these samples. These are workload-specific ratios, not fixed provider conversion rates or equal-quality task counts.

The matched window includes Muse approval reviews (2.934M tokens) and Codex auto reviews (0.637M). Excluding both gives 77.853M and 5.016M. Whether every review uses the same account allowance is unproven. Account-wide meters, integer rounding, lag, off-host use, different tokenizers/model/effort/cache mixes, and concurrent lanes limit causal attribution.

Historical Claude comparison, **20 October 00:03:48–23:57:21 UTC**: +27 weekly points (71→98), 268.071M local tokens including output, ~9.929M per weekly point. 1,150 actual model requests after excluding 3 zero synthetic records; mix 825 Sonnet + 325 Opus. This different-day workload is context, not a direct benchmark. Current Claude is stale at 99% weekly, so today's flat meter cannot establish zero-cost work. Prior audit `HUB_DAILY_QUOTA_AUDIT_2026-10-16.md` also documents historical Hub accounting defects and Claude plan changes; do not reuse Max/Pro percentages interchangeably. Gemini: +1 five-hour point, unchanged weekly in the matched window, insufficient token attribution for a comparable rate.

## Per-task evidence

Tokens can be attributed by native root and accepted-user-request boundaries, including its workers:

| Request/work segment | Input | Output |
| --- | ---: | ---: |
| Lightweight catalog benchmark request | 602,398 | 5,330 |
| Implement token reader | 5,247,154 | 27,435 |
| Wire monitoring completely | 21,506,910 | 51,429 |
| Elicitation fix + monitoring response | 7,336,631 | 29,553 |

Exact task percentages and dollar charges are not available: quota is account-wide, requests overlap, and the subscription has a fixed fee. The comparatively isolated elicitation segment (13:51:33–14:01:35) is bracketed by 13:49:17 at 95%/60% and 14:00:26 at 98%/61%: roughly **3 five-hour points / 1 weekly point**, subject to lag and the tail after the closing sample. On High, the same workload would project to ~0.6 / 0.2 points. Do not label that a precise task bill.

If fully consuming every weekly allowance, an amortized weekly point costs $5/(52/12×100) ≈ **1.15 cents** on Everyday. A comparable workload on High uses one fifth of a weekly point costing $15/(52/12×100×5) ≈ **0.69 cents**. These are capacity-allocation estimates, not marginal charges, and become higher if the plan is underused.

## Price and accounting corrections

Official Meta pricing, checked today: Everyday $5; High $15/5×; Power $50/20× and Wonder Full; Maximum $200/80×. High increases allowance, not the Spark model quality measured here. OpenAI Plus and Claude Pro list $20/month, before regional/tax differences. Meta says upgrades are immediate and prorated. See sources below.

Local settings show `contributor_mode_enabled: true`, Spark 1.3, effort high. Official Spark 1.3 Contributor API reference rates are $0.10/M fresh input, $0.002/M cached input, $0.20/M output. Applied to the entire recorded mix: approximately **$1.93 API-equivalent**. Standard reference rates yield ~$56.44. Neither is an additional subscription charge or a conversion to plan percentages. Earlier comparisons charging full standard input rates to cached Contributor input overstate value. These are counterfactual token-rate calculations; CLI subscription behavior and background-review charging need not equal PAYG.

Important audit finding: the existing `normalize_usage_record()` misses `cached_input_tokens` on automated-review events and their nested model ID. Correctly recognizing these adds **28,809,221 cached tokens** to the full-store cache total without changing total input. This audit normalizes them in its analysis script; production reader remains unchanged. Also never sum `cached_tokens` and `cache_read_tokens` (aliases here), or usage and its sibling quantity record (duplicate).

## Evidence and reproduction

- Omnigent conversations: `e64ea00517cf4804ab16710303aab660`, `09ba67f2d0fb4b10bd6396563b63b5ab`; latter is labeled Hub lane “Omnigent com muse code”.
- Native logs `~/.local/share/muse/sessions/**/session.jsonl`; read-only DB `~/.omnigent/chat.db`; quota samples `~/.omnigent/usage-history.jsonl{,.1}`; Claude ledger `~/.omnigent/claude-token-ledger.jsonl`; Codex native rollouts `~/.omnigent/codex-native/*/codex-home/sessions/2026/10/{21,22}/`.
- Reproduction scripts and frozen session/quota aggregates: `docs/analysis/muse-value-2026-10-22/`. From served repo, `mkdir -p /tmp/muse-audit` then `.venv/bin/python docs/analysis/muse-value-2026-10-22/audit.py` and `.venv/bin/python docs/analysis/muse-value-2026-10-22/codex_native.py`. Scripts write scratch data under `/tmp/muse-audit`; future execution reads then-current logs. Global record-ID dedup for Muse; native cumulative signature dedup for Codex. No model calls or paid probes were made.

Sources: [Meta plans and API rates](https://dev.meta.ai/products/muse-code), [Meta subscriptions and upgrade rules](https://dev.meta.ai/docs/muse-code/subscriptions), [OpenAI pricing and distinction between API prices and included usage](https://learn.chatgpt.com/docs/pricing), [Claude pricing](https://claude.com/pricing).

## Charts

- [Tokens versus quota consumed](analysis/muse-value-2026-10-22/tokens-vs-quota.png): measured endpoints and average slopes; High dashed as projection.
- [Tokens per quota point](analysis/muse-value-2026-10-22/tokens-per-quota-point.png): normalized comparison, five-hour and weekly separately.
- Both have SVG/PDF exports alongside them; underlying `chart-data.csv`/JSON and `chart.py` reproduce the frozen figures using system `python3` (matplotlib installed there).
