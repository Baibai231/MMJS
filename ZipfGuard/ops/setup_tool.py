"""One complete local setup: isolated dependencies, trust provisioning and builds."""
from pathlib import Path
import sys,subprocess,os
ROOT=Path(__file__).resolve().parents[1]
def main():
 subprocess.run([sys.executable,'-m','pip','install','--target',str(ROOT/'tool_deps'),'-r',str(ROOT/'requirements-tool.txt')],check=True)
 for script in ['provision_tool.py','prepare_extension.py','provision_migration.py']:
  subprocess.run([sys.executable,str(ROOT/'ops'/script)],cwd=ROOT,check=True)
 npm='npm.cmd' if os.name=='nt' else 'npm'
 subprocess.run([npm,'install','--no-audit','--no-fund'],cwd=ROOT/'apps',check=True)
 subprocess.run([npm,'run','typecheck'],cwd=ROOT/'apps',check=True)
 subprocess.run([npm,'run','build'],cwd=ROOT/'apps',check=True)
 print('Complete local build is ready. Start services with ops/tool.py start.')
if __name__=='__main__':main()
