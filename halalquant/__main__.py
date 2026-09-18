"""`python -m halalquant` runs the Rich demo, or `prepare` for the local DB."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "prepare":
        from halalquant.cli import prepare_main

        prepare_main(sys.argv[2:])
        return
    from halalquant.showcase import main as showcase_main

    showcase_main()


if __name__ == "__main__":
    main()
