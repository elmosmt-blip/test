# Changelog / What Was Changed

## Deployment fixes

- Added `.env` autoloading for dashboard/agents.
- Added `start-dashboard.sh`.
- Added `smoke-test.sh`.
- Added `verify-agents-safe.sh`.

## Safety

- Added `ALLOW_DB_WRITES` guard.
- Dashboard write endpoints are blocked when `ALLOW_DB_WRITES=0`.
- Pipeline skips DB write agents unless writes are explicitly enabled.

## Fresh news

- Agent #1 now collects fresh news for the last 30 days.
- Added strict freshness filtering and date verification.
- Added RSS fallback.

## Section routing

- Added `agents/section_router.py`.
- Agent #1 writes `editorial_type` and `target_section` to briefs.
- Agent #2 writes section routing to meta.
- Agent #6 validates section before DB insert.
- Approve no longer clears `editorial_type`.

## Publishing/rendering

- Fixed Publisher markdown line-break preservation.
- Moved AXI equipment review to Reviews section.


## Duplicate prevention

- Added `agents/dedupe.py`.
- Agent #1 excludes fresh signals already covered on the site.
- Agent #6 blocks duplicate draft creation unless `--allow-duplicate` is passed.

## Writer quality — 3-pass pipeline + deterministic linter (2026-07-14)

- Writer pipeline extended from 2 passes (draft → self-revision) to 3
  (draft → self-revision → deterministic lint + targeted repair).
- Added `agents/article_linter.py` — fast, non-LLM checks that don't depend
  on the model "remembering" an instruction: banned AI-cliché phrases
  (regex list mirrored from `agents/prompts/writer.txt`), rule-of-three
  pattern detection, sentence/paragraph rhythm variance (flags monotone
  text), missing subheadings for formats that require them, word-count
  range per format (news/insight/review/vendor), generic-title detection,
  and fact-grounding (do the brief's `key_facts` numbers actually appear in
  the article body?).
- If the linter finds issues after self-revision, `agent-02-writer.py` runs
  one targeted repair pass (new `_REPAIR_SYSTEM_PROMPT`) that fixes ONLY the
  flagged issues rather than re-revising the whole article — cheaper and
  less likely to introduce a new problem while fixing an old one. Capped at
  one repair attempt; toggle via `WRITER_LINT_REPAIR=0`.
- The linter also runs (without the repair call) in `--no-revision` mode,
  since it costs no LLM tokens — gives visibility even in the fast path.
- `article.meta.json` now carries a `lint_report` block (score, word count,
  sentence/heading counts, per-issue list) alongside the existing
  `quality_check` block from Agent #2b. Dashboard's article pane shows both
  scores side by side.
- 62 new tests: `tests/test_article_linter.py` (30, pure fixture-based) and
  `tests/test_writer_lint_repair.py` (6, orchestration with a directly
  monkeypatched `llm_client.ask_json` — avoids the `LLM_MOCK` module-import-
  order fragility found and fixed in an earlier pass). Full suite: 116/116
  passing, stable across repeated runs.
