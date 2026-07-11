"""Isolated entry point for Statistics history scanning.

The parsers are CPU-heavy Python code. Running them in a QThread still competes
for the interpreter lock and can make Qt interactions stutter. This helper runs
the same trusted local parser in a separate, below-normal-priority process and
returns only the privacy-safe numeric snapshot through a temporary JSON file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from ai_account_hub.core.benchmark_analytics import build_benchmark_analytics


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print("usage: analytics_worker_process INPUT_JSON OUTPUT_JSON", file=sys.stderr)
        return 2
    input_path, output_path = map(Path, arguments)
    try:
        # Lower Unix priority in the child. Windows priority is set by Popen in
        # the parent because os.nice is not available there.
        if os.name != "nt":
            try:
                os.nice(5)
            except OSError:
                pass
        profiles = json.loads(input_path.read_text(encoding="utf-8"))
        snapshot = build_benchmark_analytics(list(profiles or []))
        output_path.write_text(
            json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True),
            encoding="utf-8",
        )
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
