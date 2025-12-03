# System status and functionality review

## Current operational status
- The FastAPI surface exposes live status for both strategies: mean reversion status returns current prices, ratio metrics, balances, and enabled flag, while Bollinger status reports price, band levels, position, balances, and environment selection.【F:routes/mean_reversion.py†L47-L106】【F:routes/bollinger.py†L161-L225】
- Automated verification succeeds (`pytest`), indicating the current codebase is stable under the provided test suite.【80deaf†L1-L45】

## Algorithm and transaction observations
- Mean-reversion trades clamp quantities to Binance lot sizes and log trade statistics, but the execution path does not enforce the exchange `MIN_NOTIONAL` filter before submitting orders, so very small notional values could still be rejected upstream.【F:engines/mean_reversion.py†L144-L168】【F:engines/mean_reversion.py†L631-L667】
- Manual mean-reversion trades record zero fees and zero PnL, which leaves the ledger and portfolio state without realized performance information from these actions.【F:routes/mean_reversion.py†L209-L225】
- The mean-reversion status response currently hardcodes `realized_pnl_usd` to zero instead of reflecting the persisted state, so the API omits accumulated realized PnL even when it is tracked during automated rotations.【F:routes/mean_reversion.py†L87-L106】【F:engines/mean_reversion.py†L753-L816】

## Suggested functionality additions
- Add pre-trade `MIN_NOTIONAL` validation (similar to the existing lot-size clamp) to the mean-reversion execution path so that trades that are too small are rejected before hitting the exchange.【F:engines/mean_reversion.py†L144-L168】【F:engines/mean_reversion.py†L631-L667】
- Populate manual trade records with computed fees and realized PnL, and feed those values back into the portfolio state to keep monitoring consistent across automated and manual operations.【F:routes/mean_reversion.py†L209-L225】【F:engines/mean_reversion.py†L753-L816】
- Surface the stored `realized_pnl_usd` in the mean-reversion status payload so operators can see both realized and unrealized performance in the same response.【F:routes/mean_reversion.py†L87-L106】【F:engines/mean_reversion.py†L753-L816】
