"""Fixed offline MOM20/H10 exploratory study; existing panels/evaluator, no transport."""
import argparse
from contextlib import closing
from dataclasses import replace
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
CONFIG = json.loads((ROOT / 'experiment.json').read_text())
LABEL = CONFIG['classification']
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
args = parser.parse_args()
OUT = args.output.resolve()
assert not OUT.exists(), 'Refusing to overwrite any existing outputs'
DB = ROOT / CONFIG['input_relative_path']

def digest(path):
    value = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

assert digest(DB) == CONFIG['input_sha256'], 'Input identity mismatch'
cohort_path = ROOT / 'inputs/cohort_v1_manifest.json'
assert digest(cohort_path) == CONFIG['cohort_manifest_sha256']
assert json.loads(cohort_path.read_text())['tickers'] == CONFIG['cohort']
assert sha256((ROOT / 'inputs/cohort_v1_tickers.txt').read_bytes()).hexdigest() == CONFIG['membership_sha256']
input_manifest = json.loads((ROOT / 'inputs/manifest.json').read_text())
for name, expected in input_manifest['files_sha256'].items():
    assert digest(ROOT / 'inputs' / name) == expected

def forbidden(*args, **kwargs):
    raise RuntimeError('Network/provider access forbidden in this study')

socket.create_connection = socket.getaddrinfo = forbidden
socket.socket.connect = socket.socket.connect_ex = forbidden
native_connect = sqlite3.connect
connections = []

def connect(database, *args, **kwargs):
    parts = urlsplit(str(database))
    decoded = unquote(parts.path)
    if len(decoded) > 2 and decoded[0] == '/' and decoded[2] == ':':
        decoded = decoded[1:]
    assert str(database).startswith('file:') and kwargs.get('uri')
    assert Path(decoded).resolve() == DB.resolve() and parts.query == 'mode=ro', 'Only frozen read-only snapshot may be opened'
    con = native_connect(database, *args, **kwargs)
    con.execute('PRAGMA query_only=ON')
    connections.append(con)
    return con

sqlite3.connect = connect
os.environ['MARKET_DATABASE_PATH'] = str(DB)
FROZEN = ROOT / 'code_reference'
CODE_ROOT = FROZEN if FROZEN.exists() else REPO
sys.path.insert(0, str(CODE_ROOT))
import numpy as np
import pandas as pd
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.panels.observation_index import build_point_in_time_observation_index
from quantlab.panels.feature_contracts import FeatureFieldSpec, PointInTimeFeaturePanelSpec
from quantlab.panels.feature_panel import attach_features_to_observation_index
from quantlab.panels.outcome_contracts import POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1
from quantlab.panels.outcome_panel import build_point_in_time_outcome_panel
from quantlab.panels.research_dataset_contracts import POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1
from quantlab.panels.research_dataset import build_point_in_time_research_dataset
from quantlab.evaluation.panel_factor_contracts import PanelFactorEvaluationSpec, PanelFactorDirection
from quantlab.evaluation.panel_factor_analysis import evaluate_point_in_time_panel_factors


def momentum_frame(frame, sessions):
    """No fill: shift on the benchmark index, never on available stock bars."""
    index = pd.DatetimeIndex(sessions)
    closes = (frame.set_index('time')['close'].astype(float).reindex(index)
              if not frame.empty else pd.Series(np.nan, index=index))
    lag = closes.shift(20)
    valid = np.isfinite(closes) & np.isfinite(lag) & closes.gt(0) & lag.gt(0)
    mom = (closes / lag - 1).where(valid)
    reasons = np.select([closes.isna(), closes.le(0), np.arange(len(index)) < 20,
                         lag.isna(), lag.le(0)],
                        ['MISSING_CURRENT_CLOSE', 'NONPOSITIVE_CURRENT_CLOSE', 'WARMUP_BENCHMARK_SESSION',
                         'MISSING_EXACT_LAG_CLOSE', 'NONPOSITIVE_EXACT_LAG_CLOSE'], default='AVAILABLE')
    return pd.DataFrame({'time': index, 'mom20': mom.to_numpy(),
                         'stock_return_20d_pct': (100 * mom).to_numpy(),
                         'lag_session_20': pd.Series(index).shift(20).dt.strftime('%Y-%m-%d'),
                         'lag_close_20': lag.to_numpy(), 'mom20_reason': reasons})


# Focused synthetic arithmetic, exact-lag and causal-prefix checks; no market outcomes.
toy_dates = tuple(pd.date_range('2000-01-01', periods=24).strftime('%Y-%m-%d'))
toy = pd.DataFrame({'time': pd.to_datetime(toy_dates), 'close': np.arange(10., 34.)})
computed = momentum_frame(toy, toy_dates)
assert computed['mom20'].iloc[:20].isna().all()
assert computed['mom20'].iloc[20] == 30 / 10 - 1
assert pd.isna(momentum_frame(toy.iloc[1:], toy_dates)['mom20'].iloc[20])
pd.testing.assert_frame_equal(computed.iloc[:22].reset_index(drop=True),
                              momentum_frame(toy.iloc[:22], toy_dates[:22]))
changed = toy.copy()
changed.loc[22:, 'close'] = 999.
pd.testing.assert_frame_equal(computed.iloc[:22], momentum_frame(changed, toy_dates).iloc[:22])
print('Focused adapter checks PASS; freezing executed engine code before market evaluation', flush=True)

code_files = {}
for module in tuple(sys.modules.values()):
    raw = getattr(module, '__file__', None)
    if raw:
        path = Path(raw).resolve()
        if path.suffix == '.py' and path.is_relative_to(CODE_ROOT):
            relative = path.relative_to(CODE_ROOT)
            if relative.parts[0] in ('core', 'quantlab'):
                code_files[relative.as_posix()] = digest(path)
assert code_files and 'core.database' not in sys.modules and 'vnstock' not in sys.modules
if CODE_ROOT == REPO:
    FROZEN.mkdir()
    for name, expected in code_files.items():
        destination = FROZEN / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, destination)
        assert digest(destination) == expected
else:
    assert code_files == json.loads((ROOT / 'outputs/code_manifest.json').read_text())['module_sha256']
code_manifest = {'classification': LABEL, 'baseline_head': CONFIG['baseline_head'],
                 'runner_sha256': digest(Path(__file__)), 'configuration_sha256': digest(ROOT / 'experiment.json'),
                 'module_sha256': dict(sorted(code_files.items())),
                 'versions': {'python': sys.version, 'numpy': np.__version__, 'pandas': pd.__version__,
                              'sqlite': sqlite3.sqlite_version},
                 'adapter_checks': 'arithmetic, no warmup filling, exact missing lag, causal prefix/future perturbation PASS'}
code_id = sha256(canonical(code_manifest)).hexdigest()
binding = {'classification': LABEL, 'input_sha256': CONFIG['input_sha256'],
           'configuration_sha256': code_manifest['configuration_sha256'], 'code_identity': code_id}
members, benchmark, window = CONFIG['cohort'], CONFIG['benchmark'], CONFIG['window']
try:
    snapshot = build_market_data_snapshot(DB)
    benchmark_frame = snapshot.load_ohlcv([benchmark], start_date=window['start'], through_date=window['end']).frame_for(benchmark)
    sessions = tuple(benchmark_frame['time'].dt.strftime('%Y-%m-%d'))
    assert sessions == tuple(sorted(set(sessions))) and len(sessions) > 30
    context = PointInTimeUniverseContext.static(members, sessions)
    observations = build_point_in_time_observation_index(snapshot, context, benchmark_symbol=benchmark,
                    start_date=window['start'], through_date=window['end'])
    bundle = snapshot.load_ohlcv(members, through_date=window['end'])
    frames = {symbol: momentum_frame(bundle.frame_for(symbol), sessions) for symbol in members}

    class MomentumSource:
        computation_identity = sha256(canonical({'snapshot_id': snapshot.snapshot_id,
            'cohort_identity': CONFIG['cohort_identity'], 'runner_sha256': code_manifest['runner_sha256'],
            'formula': CONFIG['feature'], 'window': window})).hexdigest()
        available_symbols = bundle.available_symbols
        missing_symbols = bundle.missing_symbols
        metadata = {'output_columns': ('time', 'stock_return_20d_pct')}

        def frame_for(self, symbol):
            if symbol not in self.available_symbols:
                return pd.DataFrame(columns=['time', 'stock_return_20d_pct'])
            return frames[symbol].loc[:, ['time', 'stock_return_20d_pct']].copy()

    feature_spec = PointInTimeFeaturePanelSpec('exploratory_exact_benchmark_mom20', 'v1',
                   (FeatureFieldSpec('stock_return_20d_pct', 'stock_return_20d_pct', 'v1',
                    '100*(close(t)/close(t-20)-1); exact stored benchmark-session endpoints, no fill', 'float'),))
    features = attach_features_to_observation_index(observations, MomentumSource(), spec=feature_spec)
    outcome_spec = replace(POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1, name='exploratory_exact_session_h10', horizons=(10,))
    outcomes = build_point_in_time_outcome_panel(observations, snapshot, horizons=(10,), spec=outcome_spec)
    dataset_spec = replace(POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1, name='exploratory_mom20_h10_dataset',
                           feature_panel_spec=feature_spec, outcome_panel_spec=outcome_spec)
    dataset = build_point_in_time_research_dataset(observations, features, outcomes, spec=dataset_spec)
    evaluation_spec = PanelFactorEvaluationSpec('exploratory_mom20_h10_stock_return', 'v1',
                       ('stock_return_20d_pct',), (PanelFactorDirection.UNSPECIFIED,), (10,),
                       ('stock_forward_return_pct',), minimum_cross_section_size=CONFIG['minimum_cross_section_size'])
    print(f'Evaluating one fixed MOM20/H10 study: {len(sessions)} dates x {len(members)} fixed members', flush=True)
    evaluation = evaluate_point_in_time_panel_factors(dataset, evaluation_spec)
    frame = dataset.evaluation_frame()
    assert len(frame) == len(sessions) * 100 and len(evaluation.daily_evaluations) == len(sessions)
    assert len(evaluation.summaries) == 1
finally:
    for con in connections:
        con.close()
assert digest(DB) == CONFIG['input_sha256']
assert all(not Path(str(DB) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
assert 'core.database' not in sys.modules and 'vnstock' not in sys.modules
for name, expected in code_files.items():
    assert digest(CODE_ROOT / name) == expected
OUT.mkdir(parents=True)

def write_json(name, value):
    with (OUT / name).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')

def write_csv(name, value):
    value.to_csv(OUT / name, index=False, float_format='%.17g', lineterminator='\n', na_rep='')

detail = frame.loc[:, ['session_date', 'symbol', 'market_row_available', 'availability',
                      'stock_return_20d_pct', 'stock_return_20d_pct__availability', 'target_session_10',
                      'stock_forward_return_10_pct', 'outcome_10__availability']].copy()
detail['mom20'] = detail['stock_return_20d_pct'] / 100
detail['h10_return'] = detail['stock_forward_return_10_pct'] / 100
factor_ok = detail['stock_return_20d_pct__availability'].eq('AVAILABLE') & np.isfinite(detail['mom20'].astype(float))
outcome_ok = detail['outcome_10__availability'].eq('AVAILABLE') & np.isfinite(detail['h10_return'].astype(float))
detail['pairwise_eligible'] = factor_ok & outcome_ok
detail['exclusion_reason'] = np.select([~factor_ok & outcome_ok, factor_ok & ~outcome_ok, ~factor_ok & ~outcome_ok],
                                      ['FACTOR_UNAVAILABLE', 'H10_OUTCOME_UNAVAILABLE', 'FACTOR_AND_H10_UNAVAILABLE'], default='ELIGIBLE')
adapter_details = pd.concat([values.assign(symbol=symbol) for symbol, values in frames.items()], ignore_index=True)
adapter_details['session_date'] = adapter_details['time'].dt.strftime('%Y-%m-%d')
detail = detail.merge(adapter_details[['session_date', 'symbol', 'lag_session_20', 'lag_close_20', 'mom20_reason']],
                      on=['session_date', 'symbol'], how='left', validate='one_to_one', sort=False)
for name, value in reversed(tuple(binding.items())):
    detail.insert(0, name, value)
with (OUT / 'observations.csv.gz').open('xb') as raw:
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as stream:
            detail.to_csv(stream, index=False, float_format='%.17g', lineterminator='\n', na_rep='')
targets = dict(zip(sessions, sessions[10:] + (None,) * 10))
daily = pd.DataFrame([{**binding, 'session_date': d.signal_date, 'target_session': targets[d.signal_date],
        'cohort_count': d.total_observation_count, 'mom20_available': d.factor_usable_count,
        'h10_available': d.outcome_usable_count, 'eligible_pairs': d.pairwise_finite_eligible_count,
        'excluded_factor_only': d.excluded_for_factor_count, 'excluded_outcome_only': d.excluded_for_outcome_count,
        'excluded_both': d.excluded_for_both_count, 'rank_ic': d.rank_ic, 'undefined_reason': d.ic_undefined_reason}
        for d in evaluation.daily_evaluations])
write_csv('daily_ic.csv', daily)
s = evaluation.summaries[0]
assert int(detail['pairwise_eligible'].sum()) == s.total_eligible_observations
assert daily['eligible_pairs'].sum() == s.total_eligible_observations
assert daily['rank_ic'].notna().sum() == s.ic_defined_date_count
years = []
for year, group in daily.groupby(daily['session_date'].str[:4], sort=True):
    defined = group['rank_ic'].dropna()
    years.append({**binding, 'year': year, 'first_session': group['session_date'].iloc[0],
        'last_session': group['session_date'].iloc[-1], 'scheduled_dates': len(group),
        'defined_ic_dates': len(defined), 'eligible_pairs': int(group['eligible_pairs'].sum()),
        'equal_date_mean_ic': float(defined.mean()) if len(defined) else None,
        'positive_ic_share': float(defined.gt(0).mean()) if len(defined) else None})
write_csv('chronological_slices.csv', pd.DataFrame(years))
defined = daily.loc[daily['rank_ic'].notna()]
missingness = {name: {str(k): int(v) for k, v in detail[name].value_counts().sort_index().items()}
              for name in ('availability', 'stock_return_20d_pct__availability', 'outcome_10__availability', 'mom20_reason', 'exclusion_reason')}
missingness['undefined_daily_reasons'] = {str(k): int(v) for k, v in daily['undefined_reason'].dropna().value_counts().items()}
limits = ['Current tracked 100-ticker cohort applied retrospectively: survivorship/selection bias; no verified historical membership or ticker continuity.',
          'Unknown provider and price-adjustment basis; ratios describe stored closes, not verified RAW or total economic returns; corporate-action seams unverified.',
          'Current historical vintage, not original point-in-time data availability; revision history and source timestamps unavailable without a provenance ledger.',
          'Calendar uses only stored normalized VNINDEX sessions; official session completeness unverified.',
          'Missing exact endpoints are unavailable, never filled or substituted; intervening missing bars do not invalidate an otherwise available endpoint ratio.',
          'Overlapping H10 labels, cross-sectional/temporal dependence and variable coverage; no inferential significance, alpha or profitability confirmation.',
          'Minimum five eligible pairs is the existing software convention, not a scientific sample qualification gate.',
          'Private local input preserved; external provider retention/publication permissions remain unverified.']
summary = {**binding, 'status': 'COMPLETED', 'cohort_count': 100, 'cohort_identity': CONFIG['cohort_identity'],
    'benchmark': benchmark, 'window': window, 'snapshot_id': snapshot.snapshot_id,
    'logical_fingerprint': snapshot.logical_content_fingerprint, 'schema_version': snapshot.schema_version,
    'stored_benchmark_sessions': len(sessions), 'population_observations': len(frame),
    'market_available_observations': int(detail['market_row_available'].sum()),
    'pairwise_eligible_observations': s.total_eligible_observations,
    'observation_coverage_fraction': s.total_eligible_observations / len(frame),
    'defined_ic_dates': s.ic_defined_date_count, 'undefined_ic_dates': len(sessions) - s.ic_defined_date_count,
    'ic_coverage_fraction': s.ic_defined_date_count / len(sessions),
    'first_defined_ic_date': defined['session_date'].iloc[0] if len(defined) else None,
    'last_defined_ic_date': defined['session_date'].iloc[-1] if len(defined) else None,
    'overall_equal_date_mean_rank_ic': s.mean_daily_rank_ic, 'positive_ic_share': s.positive_ic_rate,
    'positive_ic_dates': s.positive_ic_date_count, 'zero_ic_dates': s.zero_ic_date_count,
    'negative_ic_dates': s.negative_ic_date_count,
    'eligible_pairs_per_defined_date': {'minimum': int(defined['eligible_pairs'].min()), 'maximum': int(defined['eligible_pairs'].max())},
    'daily_eligible_pair_count_distribution': {str(k): int(v) for k, v in daily['eligible_pairs'].value_counts().sort_index().items()},
    'missing_symbols': list(bundle.missing_symbols), 'missingness': missingness,
    'spec_fingerprints': {'feature': feature_spec.fingerprint, 'outcome': outcome_spec.fingerprint,
                          'dataset': dataset_spec.fingerprint, 'evaluation': evaluation_spec.fingerprint},
    'chronological_slices': years, 'limitations': limits, 'qualified': False,
    'input_unchanged': True, 'provider_requests': 0, 'parameter_search': False,
    'adx_comparison': {'directly_comparable': False, 'saved_vci_mean_ic': 0.03793022,
                       'reason': 'Saved VCI ADX study used a different input hash, 10-symbol cohort and 2024-2026 window; no paired comparison or ranking claim.'}}
write_json('code_manifest.json', code_manifest)
write_json('summary.json', summary)
lines = ['# Exploratory MOM20–H10 research note', '', '**EXPLORATORY_ONLY / LEGACY_UNVERIFIED — STATUS: COMPLETED.**', '',
    'Question: Within the fixed current 100-ticker cohort, does higher trailing 20-session momentum associate with higher subsequent 10-session close returns? The descriptive directional hypothesis is positive Rank IC; no confirmatory claim is made.', '',
    f"Input SHA256: `{CONFIG['input_sha256']}`. Snapshot ID: `{snapshot.snapshot_id}`. Capture/vintage: inputs/manifest.json. Cohort: FIXED_COHORT_V1 / EXISTING_TRACKED_UNIVERSE; exact members and hashes in inputs/cohort_v1_manifest.json and experiment.json.", '',
    f"All {len(sessions)} stored VNINDEX sessions from {sessions[0]} to {sessions[-1]} are retained with 100 expected members per session ({len(frame):,} rows). No selection by momentum, returns or missingness; no replacements.", '',
    'MOM20 = close(t)/close(t−20)−1 on the exact benchmark-session index, requiring finite positive endpoints. First 20 sessions lack the lag. H10 uses the existing outcome panel at the exact tenth subsequent stored benchmark session; last 10 sessions are censored. Feature attachment, dataset joins, availability rules and Rank IC use frozen existing engine code. The percent-scaled feature/outcome fields preserve the same ranks as fractional returns. Only this one feature/horizon is evaluated.', '',
    'Spearman Rank IC uses ascending average ranks for ties and at least five pairwise available finite observations. Constants/insufficient pairs produce undefined IC. Summary weights every defined date equally. Positive share divides strictly positive dates by all defined dates, including zeros. Calendar-year slices were fixed before outcome evaluation.', '',
    f"Equal-date mean Rank IC: **{s.mean_daily_rank_ic:.10f}**. Positive-IC share: **{s.positive_ic_rate:.4%}** ({s.positive_ic_date_count}/{s.ic_defined_date_count}; zero: {s.zero_ic_date_count}).", '',
    f"Defined ICs: **{s.ic_defined_date_count}/{len(sessions)}** ({summary['ic_coverage_fraction']:.2%}); dates {summary['first_defined_ic_date']} through {summary['last_defined_ic_date']}. Eligible pairs: **{s.total_eligible_observations:,}/{len(frame):,}** ({summary['observation_coverage_fraction']:.2%}); defined-date cross-sections range from {summary['eligible_pairs_per_defined_date']['minimum']} to {summary['eligible_pairs_per_defined_date']['maximum']}.", '',
    '| Calendar year | All dates | Defined ICs | Equal-date mean IC | Positive share |', '|---|---:|---:|---:|---:|']
lines.extend(f"| {r['year']} | {r['scheduled_dates']} | {r['defined_ic_dates']} | {r['equal_date_mean_ic']:.8f} | {r['positive_ic_share']:.2%} |" for r in years)
lines += ['', 'Missingness and daily counts are retained in summary.json, daily_ic.csv and observations.csv.gz. Detailed lag reasons distinguish warmup from absent exact endpoints. The input calendar and lineage limitations remain explicit.', '',
    'Prior VCI ADX14–H10 mean IC +0.03793022 is separate descriptive context. Its dataset hash, 10-stock cohort, evaluation dates and vintage differ; this study makes no numerical superiority claim and runs no ADX comparison. Historical exposure is not an independent validation sample.', '',
    'Limitations:', *['- ' + item for item in limits], '',
    'Reproduce from the repository root using Python 3.14.6, pandas 3.0.6 and NumPy as recorded in code_manifest.json:', '',
    '```powershell', '.\\.venv\\Scripts\\python.exe -B research/alpha_hypotheses/mom20_h10_exploratory_20261010/run_study.py --output research/alpha_hypotheses/mom20_h10_exploratory_20261010/replay_outputs', '```', '',
    'Do not rerun freeze_inputs.py. Replay reads only inputs/market.sqlite and the preserved cohort/configuration and frozen code_reference; it rejects changed inputs, code and existing output directories. Network access and any other SQLite target are prohibited. Output files bind the input, config and executed code hashes; SHA256.json records their identities. Results are deterministic with the recorded runtime.', '',
    'Conclusion: descriptive historical association only. EXPLORATORY_ONLY / LEGACY_UNVERIFIED; no p-values, confidence intervals, alpha, profitability, optimization or controlled-experiment activation.']
(OUT / 'RESEARCH_NOTE.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
hashes = {path.relative_to(ROOT).as_posix(): digest(path) for path in ROOT.rglob('*')
          if path.is_file() and path.name != 'SHA256.json' and not path.is_relative_to(ROOT / 'replay_outputs')}
write_json('SHA256.json', {'classification': LABEL, 'files': dict(sorted(hashes.items()))})
print(json.dumps({key: summary[key] for key in ('status', 'population_observations', 'pairwise_eligible_observations',
      'defined_ic_dates', 'first_defined_ic_date', 'last_defined_ic_date', 'overall_equal_date_mean_rank_ic', 'positive_ic_share')}), flush=True)
