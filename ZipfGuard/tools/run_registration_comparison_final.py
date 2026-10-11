"""Narrowed report scope and documented pre-test development amendment."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_registration_comparison as runner
from experiments.registration_risk_cap import run_extended_job


if __name__ == '__main__':
    arguments = sys.argv[1:]
    output = Path(arguments[arguments.index('--output')+1]).resolve()
    extension = output/'extension_manifest.json'
    files = [ROOT/'experiments/registration_risk_cap.py', Path(__file__).resolve()]
    record = {'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'phase': 'pre-test development amendment', 'scope': '40 final report-relevant trajectories',
              'test_observed_during_development': False}
    if extension.exists() and json.loads(extension.read_text('utf-8')) != record:
        raise RuntimeError('Extended implementation changed; cannot silently resume')
    runner.write_json(extension, record)
    runner.run_comparison_job = run_extended_job
    runner.main()
