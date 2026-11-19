"""Run from the checkout without installing the application package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from quantlab.cli import main

if __name__ == "__main__":
    main()
