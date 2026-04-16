#!/usr/bin/env python3
"""Fail CI when critical modules miss stricter coverage thresholds."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

COVERAGE_XML = "coverage.xml"
THRESHOLDS = {
    "src/utils.py": 80.0,
    "src/agents.py": 80.0,
    "main.py": 80.0,
}


def load_line_rates(path: str) -> dict[str, float]:
    tree = ET.parse(path)
    root = tree.getroot()
    rates: dict[str, float] = {}

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        line_rate_raw = class_node.attrib.get("line-rate")
        if not filename or line_rate_raw is None:
            continue

        try:
            rates[filename] = float(line_rate_raw) * 100
        except ValueError:
            continue

    return rates


def main() -> int:
    try:
        line_rates = load_line_rates(COVERAGE_XML)
    except FileNotFoundError:
        print(f"❌ Missing {COVERAGE_XML}. Run pytest coverage first.")
        return 1

    failures: list[tuple[str, float, float]] = []

    print("Critical-module coverage gates:")
    for module, threshold in THRESHOLDS.items():
        pct = line_rates.get(module)
        if pct is None:
            failures.append((module, 0.0, threshold))
            print(f"  ❌ {module}: not found in {COVERAGE_XML} (required >= {threshold:.1f}%)")
            continue

        status = "✅" if pct >= threshold else "❌"
        print(f"  {status} {module}: {pct:.1f}% (required >= {threshold:.1f}%)")
        if pct < threshold:
            failures.append((module, pct, threshold))

    if failures:
        print("\nCritical coverage gate failed.")
        return 1

    print("\nCritical coverage gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
