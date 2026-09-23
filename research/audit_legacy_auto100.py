from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from market_data.contract_filters import (
    RESEARCH_CORE_SYMBOLS,
    deterministic_symbol_sample,
    is_rwa_contract,
)
from market_data.universe_collector import BitgetClient

OUT = Path("research/results/legacy_auto100_audit.json")


def main() -> None:
    client = BitgetClient()
    contracts = client.active_usdt_perpetuals()
    by_symbol = {str(x["symbol"]): x for x in contracts}

    legacy_symbols = deterministic_symbol_sample(
        by_symbol,
        100,
        core_symbols=RESEARCH_CORE_SYMBOLS,
        seed="bb-research-v1",
    )
    crypto_symbols = [s for s, x in by_symbol.items() if not is_rwa_contract(x)]
    corrected_symbols = deterministic_symbol_sample(
        crypto_symbols,
        100,
        core_symbols=RESEARCH_CORE_SYMBOLS,
        seed="bb-research-v1",
    )

    legacy_rwa = [s for s in legacy_symbols if is_rwa_contract(by_symbol[s])]
    overlap = sorted(set(legacy_symbols) & set(corrected_symbols))
    dropped = [s for s in legacy_symbols if s not in set(corrected_symbols)]
    added = [s for s in corrected_symbols if s not in set(legacy_symbols)]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_all": len(contracts),
        "active_crypto": len(crypto_symbols),
        "active_rwa": sum(1 for x in contracts if is_rwa_contract(x)),
        "legacy_auto100_rwa_count": len(legacy_rwa),
        "legacy_auto100_rwa_pct": 100.0 * len(legacy_rwa) / len(legacy_symbols),
        "legacy_auto100_rwa_symbols": legacy_rwa,
        "corrected_overlap_count": len(overlap),
        "corrected_overlap_pct": 100.0 * len(overlap) / 100.0,
        "legacy_dropped_count": len(dropped),
        "corrected_added_count": len(added),
        "legacy_dropped": dropped,
        "corrected_added": added,
        "legacy_symbols": legacy_symbols,
        "corrected_symbols": corrected_symbols,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
