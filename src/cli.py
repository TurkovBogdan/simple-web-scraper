"""Entry point — starts the lightweight resident process."""
from __future__ import annotations

from server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
