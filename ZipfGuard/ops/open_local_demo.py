"""Open an intentionally separate LOCAL-only browser profile for manual use.

No operating-system certificate trust or normal browser profile is modified.
External hostnames are disabled in this profile. Prefer real public TLS in deploy.
"""
from pathlib import Path
import json,subprocess,os,argparse
ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--browser');args=parser.parse_args()
 candidates=[Path(args.browser)] if args.browser else [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
 # Extension flags require developer-capable Chromium; prefer the already
 # installed QA Chromium if the maintained system build disables sideloading.
 installed=Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright'
 if not args.browser and installed.is_dir():
  candidates=list(installed.glob('chromium-*/chrome-win64/chrome.exe'))+candidates
 browser=next((path for path in candidates if path.is_file()),None)
 if not browser:raise SystemExit('Pass --browser pointing to a developer-capable Chromium executable.')
 cfg=json.loads((ROOT/'tool_runtime/installation.json').read_text('utf-8'));extension=ROOT/'apps/user-assistant/dist'
 command=[str(browser),'--user-data-dir='+str(ROOT/'tool_runtime/manual-browser'),'--ignore-certificate-errors',
          '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
          '--disable-extensions-except='+str(extension),'--load-extension='+str(extension),
          cfg['admin_api'],'chrome-extension://'+cfg['extension_id']+'/index.html',cfg['reference_site']]
 subprocess.Popen(command)
 print('Opened a separate local-only browser profile. Normal browser trust and data are unchanged.')
if __name__=='__main__':main()
