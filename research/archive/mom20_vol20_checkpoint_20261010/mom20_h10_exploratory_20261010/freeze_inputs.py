"""One-time local byte preservation for the fixed MOM20/H10 study; no transport."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
SOURCE = REPO / 'data/market.db'
COHORT = Path(r'C:\Users\hello\.codex\visualizations\2026\10\08\01a11bfe-dcc4-7163-b338-1d1e66c085e8\fixed_cohort_v1_existing_tracked_universe_20261009\cohort_v1_manifest.json')
LABEL = 'EXPLORATORY_ONLY / LEGACY_UNVERIFIED'

def digest(path):
    value = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()

def state(path):
    if not path.exists():
        return None
    stat = path.stat()
    return {'sha256': digest(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args],
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}).decode()

assert not (ROOT / 'inputs').exists(), 'Never replace preserved inputs'
assert not (ROOT / 'experiment.json').exists(), 'Never replace fixed configuration'
before = {name: state(REPO / name) for name in set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0')) - {''}
          if not (REPO / name).is_relative_to(ROOT)}
index = REPO / git('rev-parse', '--git-path', 'index').strip()
git_before = {'head': git('rev-parse', 'HEAD').strip(), 'branch': git('branch', '--show-current').strip(),
              'refs': git('show-ref'), 'index': state(index),
              'status': git('status', '--porcelain=v1', '--untracked-files=all')}
protected = [p for db in (REPO / 'data').rglob('*.db')
             for p in (db, *(Path(str(db) + suffix) for suffix in ('-wal', '-shm', '-journal')))]
databases = {str(path): state(path) for path in protected}
assert SOURCE.is_file()
for suffix in ('-wal', '-journal'):
    path = Path(str(SOURCE) + suffix)
    assert not path.exists() or path.stat().st_size == 0, 'Active market sidecar requires a WAL-aware preservation step'
assert digest(COHORT) == '503959d0d300963130fc16274ef28ff293d8d01975beac73053d20685b8cfb27'
cohort = json.loads(COHORT.read_text())
members = cohort['tickers']
ticker_bytes = ('\n'.join(members) + '\n').encode()
assert len(members) == len(set(members)) == 100 and 'VNINDEX' not in members
assert sha256(ticker_bytes).hexdigest() == cohort['membership_sha256'] == 'a56872b859b018d8a8dfb7f034b15ceb9a68ea2805aeb5c7150fa53204d14faf'
inputs = ROOT / 'inputs'
inputs.mkdir()
shutil.copyfile(SOURCE, inputs / 'market.sqlite')
shutil.copyfile(COHORT, inputs / 'cohort_v1_manifest.json')
(inputs / 'cohort_v1_tickers.txt').write_bytes(ticker_bytes)
assert digest(inputs / 'market.sqlite') == digest(SOURCE)
with closing(sqlite3.connect((inputs / 'market.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
    assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    bounds = connection.execute("SELECT MIN(date(time)),MAX(date(time)),COUNT(DISTINCT date(time)) FROM prices WHERE UPPER(TRIM(symbol))='VNINDEX'").fetchone()
assert bounds[0] and bounds[1] and bounds[2] > 30
observation_path = os.environ.get('MARKET_OBSERVATION_LOG_PATH', '').strip()
provenance = Path(observation_path).expanduser() if observation_path else SOURCE.with_name('market_observations.db')
if not provenance.is_absolute():
    provenance = REPO / provenance
# This historical study never initializes provenance or retrospectively qualifies a baseline.
assert not provenance.exists(), 'A present provenance ledger must be inspected and preserved before execution'
config = {'classification': LABEL, 'study': 'exploratory_mom20_h10.v1', 'baseline_head': git_before['head'],
          'input_relative_path': 'inputs/market.sqlite', 'input_sha256': digest(inputs / 'market.sqlite'),
          'cohort_manifest_sha256': digest(inputs / 'cohort_v1_manifest.json'),
          'cohort_identity': cohort['cohort_identity'], 'membership_sha256': cohort['membership_sha256'],
          'cohort': members, 'benchmark': 'VNINDEX', 'window': {'start': bounds[0], 'end': bounds[1]},
          'window_policy': 'all available stored VNINDEX benchmark sessions; chosen before momentum/outcome evaluation',
          'feature': 'MOM20 = close(t)/close(t-20)-1; exact stored benchmark sessions; no filling',
          'feature_engine_field': 'stock_return_20d_pct = 100*MOM20; study-specific exact-benchmark-session feature spec',
          'warmup_sessions': 20, 'horizon_sessions': 10, 'outcome': 'stock_forward_return_10_pct / 100',
          'minimum_cross_section_size': 5, 'rank_ic': 'existing ascending average-tie ranks, Pearson correlation, pairwise finite and AVAILABLE',
          'aggregation': 'equal-date mean over defined ICs only; all undefined dates preserved',
          'positive_ic_share': 'strictly positive ICs / defined IC dates; zero ICs stay in denominator',
          'chronological_slices': 'calendar years, fixed before outcomes', 'price_basis': 'UNKNOWN',
          'scientifically_qualified': False, 'provider_requests': False, 'parameter_search': False}
manifest = {'classification': LABEL, 'captured_at_utc': datetime.now(timezone.utc).isoformat(),
            'source_path': str(SOURCE), 'source_file_state': databases[str(SOURCE)],
            'snapshot_sha256': config['input_sha256'], 'snapshot_method': 'byte-identical copy; no source WAL/journal content; only private copy opened with SQLite',
            'cohort_original_path': str(COHORT), 'cohort_manifest_sha256': config['cohort_manifest_sha256'],
            'source_provenance_log': {'path': str(provenance), 'available': False},
            'source_provider': 'LEGACY_UNVERIFIED; no source ledger available', 'price_basis': 'UNKNOWN',
            'retention': 'private local preservation authorized for this task; no publication or provider retention-rights claim',
            'files_sha256': {p.name: digest(p) for p in inputs.iterdir()}}
(ROOT / 'experiment.json').write_text(json.dumps(config, indent=2) + '\n')
(inputs / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
assert {str(path): state(path) for path in protected} == databases
assert {name: state(REPO / name) for name in before} == before
assert state(index) == git_before['index']
(ROOT / 'preservation_before.json').write_text(json.dumps({'git': git_before, 'files': before, 'databases': databases}, indent=2) + '\n')
print(json.dumps({'input_sha256': config['input_sha256'], 'window': config['window'],
                  'stored_benchmark_session_count': bounds[2], 'cohort_count': len(members),
                  'source_unchanged': True, 'provenance_log_available': False}))
