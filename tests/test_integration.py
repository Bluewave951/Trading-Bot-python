"""E2E tests — Phase 7.

The data -> signal -> alert pipeline is wired up now (`src/scheduler.py`,
exercised via `main.py`'s one-shot `run_once()` and `python -m src.scheduler`
for continuous operation); `tests/test_scheduler.py` covers that wiring
(fetch -> indicators -> signal -> persist -> notify) with the network and
signal-generation boundaries faked out, same isolation style as a proper
E2E test. What's still TODO here, per TRADING_BOT_PLAN.md's Phase 7
checklist: an actual paper-trading run (no live money) and comparing a
week of generated signals against manual chart analysis — both need a
real multi-day run, not something a single pytest session can cover.
"""
