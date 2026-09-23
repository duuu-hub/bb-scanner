import hashlib

"""Shared Bitget contract-universe filters.

Automatic scanners/research universes default to crypto-only USDT perpetuals.
Explicit user-supplied symbols may still be tested separately by callers.
"""

RESEARCH_CORE_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "SUIUSDT", "PEPEUSDT", "WIFUSDT",
    "龙虾USDT", "NILUSDT", "INITUSDT", "METISUSDT",
)


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


def deterministic_symbol_sample(
    active_symbols,
    target: int,
    *,
    core_symbols=(),
    seed: str = "bb-research-v1",
) -> list[str]:
    """Deterministic broad sample with optional fixed core symbols."""
    target = max(1, int(target))
    active = sorted(set(str(x) for x in active_symbols if x))
    active_set = set(active)

    chosen = []
    seen = set()
    for symbol in core_symbols:
        symbol = str(symbol)
        if symbol in active_set and symbol not in seen:
            chosen.append(symbol)
            seen.add(symbol)

    others = [s for s in active if s not in seen]
    others.sort(
        key=lambda x: hashlib.sha256(
            (seed + "|" + x).encode("utf-8")
        ).hexdigest()
    )
    chosen.extend(others[:max(0, target - len(chosen))])
    return chosen[:target]
