#!/usr/bin/env python3
"""Run the bundled DataLab CLI without installing the package itself."""
from datalab.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
