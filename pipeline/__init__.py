"""Track 1 Centene rate-file curation pipeline."""
"""Pipeline package bootstrap for dependencies bundled with the project."""

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_RUNTIME = _PROJECT_ROOT / "data" / "runtime_packages"
if _BUNDLED_RUNTIME.is_dir() and str(_BUNDLED_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_BUNDLED_RUNTIME))
