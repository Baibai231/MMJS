"""Local operator creates a one-use owner invitation; secret never printed."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import json,secrets,time,argparse
from services.common.store import Store
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--disable-test-identities',action='store_true');args=parser.parse_args()
 cfg=json.loads((ROOT/'tool_runtime/admin/config.json').read_text('utf-8'));store=Store(cfg['store'])
 value=secrets.token_urlsafe(32);store.put('invitations',value,{'identifier':'*','role':'owner','expires':time.time()+86400})
 (ROOT/'tool_runtime/administrator-installation-code.txt').write_text(value,encoding='utf-8')
 if args.disable_test_identities:
  for name,row in store.list('administrators').items():
   if name.startswith('qa-'):row['enabled']=False;store.put('administrators',name,row)
 print('One-use owner invitation written to the private installation-code file; valid for 24 hours.')
