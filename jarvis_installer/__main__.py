from __future__ import annotations

import sys
from .cli import app, run_tui


def main():
    # If no args, run TUI
    if len(sys.argv) == 1:
        run_tui()
    else:
        app()


if __name__ == "__main__":
    main()