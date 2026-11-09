"""DataLab execution helpers. No network or installation side effects on import."""
import sys

# Avoid writing bytecode into the installed skill or an existing environment.
sys.dont_write_bytecode = True
__version__ = "0.3.0"
