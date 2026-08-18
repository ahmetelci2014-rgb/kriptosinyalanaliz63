# Panel Module Map — V3.32.9

Date: 2026-08-18  
Purpose: safe dependency map before any dashboard file deletion.  
Rule: no trading/strategy/Telegram/state/ledger behavior is changed by this document.

## ACTIVE ENTRY

- `dashboard_app.py` — stable Render/Docker entry point.
- `dashboard_share_runtime_app.py` — current V3.32.9 runtime selected by `dashboard_app.py`.

These files must not be deleted or renamed while V3.32.9 is active.

## ACTIVE DIRECT DEPENDENCIES OF V3.32.9

`dashboard_share_runtime_app.py` directly imports:

- `dashboard_accountflow_runtime_app.py`
- `dashboard_accounts_app.py`
- `dashboard_chartfix_app.py`
- `dashboard_commercial_app.py`
- `dashboard_earlyperformance_app.py`
- `dashboard_market_app.py`
- `dashboard_sharecard_app.py`
- `dashboard_shareui_app.py`
- `dashboard_live_app.py`

These are ACTIVE.

## ACTIVE TRANSITIVE RUNTIME CHAIN

`dashboard_accountflow_runtime_app.py` continues the runtime through:

- `dashboard_account_flow_app.py`
- `dashboard_accounts_app.py`
- `dashboard_chartfix_app.py`
- `dashboard_commercial_app.py`
- `dashboard_earlyperformance_app.py`
- `dashboard_market_app.py`
- `dashboard_runtimefix_app.py`
- `dashboard_score_app.py`
- `dashboard_surface_parity_app.py`
- `dashboard_watchsync_app.py`
- `dashboard_live_app.py`

`dashboard_runtimefix_app.py` continues through:

- `dashboard_opportunity_app.py`
- `dashboard_runtimefix_v3321_base.py`
- `dashboard_score_app.py`
- `dashboard_surface_parity_app.py`
- `dashboard_mobile_server_app.py` (runtime import on mobile path)
- the shared account/chart/commercial/market/live modules listed above

All modules in this transitive chain are ACTIVE until a static import graph and full dashboard regression run proves otherwise.

## TEST-ONLY

- Files named `test_dashboard_*.py` are regression tests, not runtime entry points.
- They must not be deleted merely because they are not imported by the runtime.
- They belong to the separate `Kripto Panel Kontrolü` workflow.

## LEGACY CANDIDATE — DO NOT DELETE YET

Any `dashboard_*.py` file that is:

1. not `dashboard_app.py`,
2. not in the ACTIVE direct/transitive chain above,
3. not imported dynamically by an ACTIVE module,
4. and not required by `test_dashboard_*.py`,

is only a **LEGACY CANDIDATE**, not confirmed dead code.

No bulk deletion is allowed until all four checks pass.

### Spot checks completed

The first legacy-looking mobile layers were searched before deletion:

- `dashboard_simplevoice_app.py` is referenced by `dashboard_mobileux_app.py` and its regression tests.
- `dashboard_mobileux_app.py` is referenced by `dashboard_touchguard_app.py` and its regression tests.
- These findings show that apparently old version layers still form dependency/test chains.

Decision: **do not delete these files now**. A filename looking old is not sufficient evidence of dead code.

## SAFE CLEANUP PROCEDURE

Before deleting a legacy candidate:

1. Search all Python imports and dynamic import strings for the filename/module.
2. Search all `test_dashboard_*.py` references.
3. Check `Dockerfile.dashboard` copy list.
4. Run `python -m py_compile dashboard_*.py test_dashboard_*.py`.
5. Run all dashboard regression tests.
6. Build `Dockerfile.dashboard`.
7. Start `dashboard_app.py` and verify `/healthz`, login, FREE, PREMIUM, ADMIN, mobile, market, account and share flows.
8. Delete only in a separate rollback-safe commit.

## CURRENT DECISION

- ACTIVE chain: preserve.
- TEST-ONLY: preserve.
- LEGACY candidates: no deletion yet; spot checks found real dependency/test chains.
- Reason: the panel is currently stable and file deletion has no direct signal-quality benefit.
- Revisit deletion only when a candidate has zero runtime references, zero test references, and passes the full panel regression/Docker/runtime checks.

This map is the source of truth for the next panel cleanup pass.
