"""Renew only LOCAL test certificates; do not change receipt identities/state."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from ops.local_tls import renew

if __name__=='__main__':
    renew(ROOT/'tool_runtime')
    print('Local TLS renewed; signing identities and data preserved.')
