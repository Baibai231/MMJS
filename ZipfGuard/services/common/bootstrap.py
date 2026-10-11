"""Add workspace-local, isolated dependency directories before imports."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / 'privacy_deps', ROOT / 'tool_deps'):
    sys.path.insert(0, str(directory))
