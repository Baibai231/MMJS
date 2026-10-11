"""Public trust configuration signed by the independently distributed package publisher."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import json
from services.common.security import create_private,public_hex
from privacy.transaction_contract import canonical
if __name__=='__main__':
 runtime=ROOT/'tool_runtime';key=create_private(runtime/'extension-publisher.key')
 public=ROOT/'apps/user-assistant/public';stations=json.loads((public/'trusted-stations.json').read_text('utf-8'))
 (public/'trusted-publishers.json').write_text(json.dumps([public_hex(key)]),encoding='utf-8')
 descriptor={'publisher':public_hex(key),'station':stations[0]};descriptor['signature']=key.sign(canonical(descriptor['station'])).hex()
 (runtime/'signed-station.json').write_text(json.dumps(descriptor,ensure_ascii=False,indent=2),encoding='utf-8')
 manifest=json.loads((public/'manifest.json').read_text('utf-8'));manifest['optional_host_permissions']=['https://*/*'];manifest['permissions']=['storage']
 (public/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 print('Signed public station descriptor prepared; publisher private key remains local to package maintainer.')
