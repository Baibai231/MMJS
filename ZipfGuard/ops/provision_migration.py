"""Install local relying-party migration identity; only public key reaches nodes."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from services.common.security import create_private,public_hex
import json
def provision():
 runtime=ROOT/'tool_runtime';key=create_private(runtime/'reference/migration.key')
 for folder in ['user','admin','party_0','party_1','party_2']:
  path=runtime/folder/'config.json';cfg=json.loads(path.read_text('utf-8'));cfg['migration_issuer']=public_hex(key);cfg.setdefault('reserved_legacy_accounts',['legacy-user-001'])
  path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
 print('Relying-party migration public identity configured; private key remains with reference website.')
if __name__=='__main__':provision()
