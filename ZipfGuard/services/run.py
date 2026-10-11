from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,json,uvicorn
from services.common.business import Business

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--kind',choices=['user','admin'],required=True)
    args=parser.parse_args();cfg=json.loads(Path(args.config).read_text('utf-8'));business=Business(cfg,args.kind)
    uvicorn.run(business.app,host=cfg.get('host','127.0.0.1'),port=cfg['port'],ssl_certfile=cfg['cert'],ssl_keyfile=cfg['tls_key'],log_level='warning',access_log=False)

if __name__=='__main__':main()
