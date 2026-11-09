"""Support python -m datalab alongside the script and console entry points."""
from .cli import main

raise SystemExit(main())
