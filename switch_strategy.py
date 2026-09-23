import json
import sys
from pathlib import Path

CONFIG_PATH = Path("config/trading_config.json")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python switch_strategy.py OFF|BULL|RANGE|BEAR")

    target = sys.argv[1].upper()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    allowed = {str(x).upper() for x in cfg.get("allowed_strategies", [])}
    if target != "OFF" and target not in allowed:
        raise SystemExit(f"unsupported strategy: {target}")

    previous = str(cfg.get("active_strategy", "OFF")).upper()
    cfg["active_strategy"] = target
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"active_strategy: {previous} -> {target}")
    print(f"demo_auto_execute remains: {cfg.get('demo_auto_execute', False)}")


if __name__ == "__main__":
    main()
