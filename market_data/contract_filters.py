"""Shared Bitget contract-universe filters.

Automatic scanners/research universes default to crypto-only USDT perpetuals.
Explicit user-supplied symbols may still be tested separately by callers.
"""


def is_rwa_contract(item: dict) -> bool:
    return str(item.get("isRwa", "")).strip().upper() == "YES"


def is_active_usdt_perpetual(item: dict, *, include_rwa: bool = False) -> bool:
    if item.get("symbolType") != "perpetual":
        return False
    if item.get("symbolStatus") != "normal":
        return False
    if str(item.get("quoteCoin", "")).upper() != "USDT":
        return False
    if not item.get("symbol"):
        return False
    if not include_rwa and is_rwa_contract(item):
        return False
    return True


def active_symbols_from_contracts(
    contracts,
    *,
    include_rwa: bool = False,
) -> list[str]:
    return sorted(
        {
            str(item["symbol"])
            for item in (contracts or [])
            if is_active_usdt_perpetual(item, include_rwa=include_rwa)
        }
    )
