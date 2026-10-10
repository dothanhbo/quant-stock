"""One fixed offline VOL20/H10 study; generic existing panels and Rank IC primitives."""
import argparse
from dataclasses import replace
import gzip
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import sys
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'experiment.json').read_text())
LABEL = CONFIG['classification']
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
args = parser.parse_args()
OUT = args.output.resolve()
assert not OUT.exists(), 'Never overwrite existing research evidence'
DB = (ROOT / CONFIG['input_relative_path']).resolve()
ENGINE = (ROOT / CONFIG['engine_root_relative_path']).resolve()
ENGINE_MANIFEST = (ROOT / CONFIG['engine_manifest_relative_path']).resolve()

def digest(path):
    h = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

assert digest(DB) == CONFIG['input_sha256']
cohort_file = (ROOT / CONFIG['cohort_manifest_relative_path']).resolve()
assert digest(cohort_file) == CONFIG['cohort_manifest_sha256']
cohort = json.loads(cohort_file.read_text())
assert cohort['tickers'] == CONFIG['cohort'] and len(set(CONFIG['cohort'])) == 100
assert sha256(('\n'.join(CONFIG['cohort']) + '\n').encode()).hexdigest() == CONFIG['membership_sha256']
engine_manifest = json.loads(ENGINE_MANIFEST.read_text())
for name, value in engine_manifest['module_sha256'].items():
    assert digest(ENGINE / name) == value

def forbidden(*args, **kwargs):
    raise RuntimeError('Networking/provider access forbidden')

socket.create_connection = socket.getaddrinfo = forbidden
socket.socket.connect = socket.socket.connect_ex = forbidden
native_connect = sqlite3.connect
connections = []

def connect(database, *args, **kwargs):
    parts = urlsplit(str(database))
    path = unquote(parts.path)
    if len(path) > 2 and path[0] == '/' and path[2] == ':':
        path = path[1:]
    assert str(database).startswith('file:') and kwargs.get('uri')
    assert Path(path).resolve() == DB and parts.query == 'mode=ro', 'Only the frozen snapshot is allowed'
    con = native_connect(database, *args, **kwargs)
    con.execute('PRAGMA query_only=ON')
    connections.append(con)
    return con

sqlite3.connect = connect
os.environ['MARKET_DATABASE_PATH'] = str(DB)
sys.path.insert(0, str(ENGINE))
import numpy as np
import pandas as pd
from quantlab.catalog.market_data_snapshot import MarketDataBundle, build_market_data_snapshot
from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.panels.observation_index import build_point_in_time_observation_index
from quantlab.panels.feature_contracts import FeatureFieldSpec, PointInTimeFeaturePanelSpec
from quantlab.panels.feature_panel import attach_features_to_observation_index
from quantlab.panels.outcome_contracts import POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1
from quantlab.panels.outcome_panel import build_point_in_time_outcome_panel
from quantlab.panels.research_dataset_contracts import POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1
from quantlab.panels.research_dataset import build_point_in_time_research_dataset
from quantlab.evaluation.panel_factor_analysis import _average_ranks, _pearson, _mean


def volatility_frame(frame, sessions):
    """Twenty complete simple returns on the benchmark index, ending at t."""
    index = pd.DatetimeIndex(sessions)
    close = (frame.set_index('time')['close'].astype(float).reindex(index)
             if not frame.empty else pd.Series(np.nan, index=index))
    prior = close.shift(1)
    valid = np.isfinite(close) & np.isfinite(prior) & close.gt(0) & prior.gt(0)
    returns = (close / prior - 1).where(valid)
    vol = returns.rolling(20, min_periods=20).std(ddof=1)
    count = returns.rolling(20, min_periods=1).count().fillna(0).astype(int)
    reason = np.select([close.isna(), close.le(0), np.arange(len(index)) < 20,
                        count.lt(20), ~np.isfinite(vol)],
                       ['MISSING_CURRENT_CLOSE', 'NONPOSITIVE_CURRENT_CLOSE', 'WARMUP_BENCHMARK_SESSION',
                        'INCOMPLETE_EXACT_20_RETURN_WINDOW', 'NONFINITE_VOL20'], default='AVAILABLE')
    return pd.DataFrame({'time': index, 'daily_return': returns.to_numpy(),
                         'vol20': vol.to_numpy(), 'neg_vol20': (-vol).to_numpy(),
                         'return_count_20': count.to_numpy(), 'vol20_reason': reason})


def rank_ic(scores, outcomes, minimum=5):
    """Reuse unchanged Rank IC primitives and existing undefined conventions."""
    if len(scores) < minimum:
        return None, 'fewer_than_minimum_pairwise_finite_observations'
    constant_score, constant_outcome = len(set(scores)) == 1, len(set(outcomes)) == 1
    if constant_score and constant_outcome:
        return None, 'constant_factor_and_outcome'
    if constant_score:
        return None, 'constant_factor'
    if constant_outcome:
        return None, 'constant_outcome'
    value = _pearson(_average_ranks(scores), _average_ranks(outcomes))
    return value, None if value is not None else 'correlation_denominator_zero'


# Independent arithmetic fixture: ten +1% and ten -1% returns, sample variance /19.
toy_sessions = tuple(pd.bdate_range('2000-01-03', periods=31).strftime('%Y-%m-%d'))
independent_returns = [0.01 if index % 2 == 0 else -0.01 for index in range(30)]
toy_closes = [100.]
for value in independent_returns:
    toy_closes.append(toy_closes[-1] * (1 + value))
toy = pd.DataFrame({'time': pd.to_datetime(toy_sessions), 'close': toy_closes})
calculated = volatility_frame(toy, toy_sessions)
expected = math.sqrt((10 * 0.01 ** 2 + 10 * (-0.01) ** 2) / 19)
assert calculated['vol20'].iloc[:20].isna().all()
assert math.isclose(calculated['vol20'].iloc[20], expected, rel_tol=1e-12, abs_tol=1e-14)
assert calculated['neg_vol20'].iloc[20] == -calculated['vol20'].iloc[20]
assert pd.isna(volatility_frame(toy.drop(index=10), toy_sessions)['vol20'].iloc[20])
pd.testing.assert_frame_equal(calculated.iloc[:24].reset_index(drop=True), volatility_frame(toy.iloc[:24], toy_sessions[:24]))
future_changed = toy.copy()
future_changed.loc[24:, 'close'] = 999.
pd.testing.assert_frame_equal(calculated.iloc[:24], volatility_frame(future_changed, toy_sessions).iloc[:24])
assert _average_ranks((1., 1., 2.)) == (1.5, 1.5, 3.)
assert rank_ic((0.,) * 5, (1., 2., 3., 4., 5.))[1] == 'constant_factor'
assert rank_ic((1., 2.), (2., 1.))[0] is None

outcome_spec = replace(POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1, name='exploratory_exact_session_h10', horizons=(10,))

class FixtureSnapshot:
    snapshot_id = 'synthetic_exact_session_label_fixture'
    canonical_db_path = Path('synthetic_never_opened.sqlite')
    first_session_date, last_session_date = toy_sessions[0], toy_sessions[-1]

    def load_ohlcv(self, symbols, *, start_date=None, through_date=None):
        frames = {}
        for symbol in sorted(set(symbols)):
            values = pd.DataFrame({'symbol': symbol, 'time': pd.to_datetime(toy_sessions),
                                   'close': np.arange(50., 81.) if symbol == 'AAA' else np.arange(100., 131.)})
            for field in ('open', 'high', 'low'):
                values[field] = values['close']
            values['volume'] = 100
            if symbol == 'AAA':
                values = values.drop(index=10)  # missing exact target must never slide to t+11
            if start_date:
                values = values.loc[values['time'] >= pd.Timestamp(start_date)]
            if through_date:
                values = values.loc[values['time'] <= pd.Timestamp(through_date)]
            frames[symbol] = values.reset_index(drop=True)
        symbols = tuple(sorted(frames))
        return MarketDataBundle(symbols, symbols, (), start_date, through_date,
                                sum(len(f) for f in frames.values()), MappingProxyType(frames))

fixture = FixtureSnapshot()
fixture_index = build_point_in_time_observation_index(fixture, PointInTimeUniverseContext.static(('AAA',), toy_sessions),
                 benchmark_symbol='VNINDEX', start_date=toy_sessions[0], through_date=toy_sessions[-1])
fixture_outcomes = build_point_in_time_outcome_panel(fixture_index, fixture, horizons=(10,), spec=outcome_spec).frame
first = fixture_outcomes.loc[fixture_outcomes['session_date'].eq(toy_sessions[0])].iloc[0]
second = fixture_outcomes.loc[fixture_outcomes['session_date'].eq(toy_sessions[1])].iloc[0]
assert first['target_session_10'] == toy_sessions[10]
assert first['outcome_10__availability'] == 'MISSING_STOCK_TARGET_CLOSE'
assert second['target_session_10'] == toy_sessions[11]
assert math.isclose(second['stock_forward_return_10_pct'] / 100, 61 / 51 - 1, abs_tol=1e-14)
assert fixture_outcomes.iloc[-1]['outcome_10__availability'] == 'CENSORED_AFTER_DATA_END'
validation = {'classification': LABEL, 'sample_std_fixture_expected': expected,
              'sample_std_fixture_actual': float(calculated['vol20'].iloc[20]),
              'ddof_1_and_20_returns': 'PASS', 'missing_intermediate_bar_no_fill': 'PASS',
              'causal_prefix_and_future_perturbation': 'PASS', 'exact_h10_no_target_substitution': 'PASS',
              'rank_ties_constant_and_minimum_size': 'PASS', 'canonical_sqlite_connections': 0}
print('Focused VOL20 arithmetic, causality, exact H10 and Rank IC checks PASS', flush=True)

code_files = {}
for module in tuple(sys.modules.values()):
    raw = getattr(module, '__file__', None)
    if raw:
        path = Path(raw).resolve()
        if path.suffix == '.py' and path.is_relative_to(ENGINE):
            code_files[path.relative_to(ENGINE).as_posix()] = digest(path)
assert code_files == engine_manifest['module_sha256']
assert 'core.database' not in sys.modules and 'vnstock' not in sys.modules
code_manifest = {'classification': LABEL, 'baseline_head': CONFIG['baseline_head'],
                 'runner_sha256': digest(Path(__file__)), 'configuration_sha256': digest(ROOT / 'experiment.json'),
                 'engine_manifest_sha256': digest(ENGINE_MANIFEST), 'module_sha256': code_files,
                 'versions': {'python': sys.version, 'pandas': pd.__version__, 'numpy': np.__version__, 'sqlite': sqlite3.sqlite_version},
                 'rank_ic_reuse': 'Generic named feature panel; unchanged _average_ranks, _pearson and _mean. Built-in factor whitelist unchanged.'}
code_id = sha256(canonical(code_manifest)).hexdigest()
binding = {'classification': LABEL, 'input_sha256': CONFIG['input_sha256'],
           'configuration_sha256': code_manifest['configuration_sha256'], 'code_identity': code_id}
members, window, benchmark = CONFIG['cohort'], CONFIG['window'], CONFIG['benchmark']
try:
    snapshot = build_market_data_snapshot(DB)
    benchmark_frame = snapshot.load_ohlcv([benchmark], start_date=window['start'], through_date=window['end']).frame_for(benchmark)
    sessions = tuple(benchmark_frame['time'].dt.strftime('%Y-%m-%d'))
    context = PointInTimeUniverseContext.static(members, sessions)
    observations = build_point_in_time_observation_index(snapshot, context, benchmark_symbol=benchmark,
                   start_date=window['start'], through_date=window['end'])
    bundle = snapshot.load_ohlcv(members, through_date=window['end'])
    frames = {symbol: volatility_frame(bundle.frame_for(symbol), sessions) for symbol in members}

    class VolatilitySource:
        computation_identity = sha256(canonical({'snapshot_id': snapshot.snapshot_id, 'cohort_identity': CONFIG['cohort_identity'],
                               'formula': CONFIG['feature'], 'score': CONFIG['score'], 'runner_sha256': code_manifest['runner_sha256']})).hexdigest()
        available_symbols, missing_symbols = bundle.available_symbols, bundle.missing_symbols
        metadata = {'output_columns': ('time', 'neg_vol20')}

        def frame_for(self, symbol):
            return (frames[symbol][['time', 'neg_vol20']].copy() if symbol in self.available_symbols
                    else pd.DataFrame(columns=['time', 'neg_vol20']))

    feature_spec = PointInTimeFeaturePanelSpec('exploratory_negative_vol20', 'v1',
                   (FeatureFieldSpec('neg_vol20', 'neg_vol20', 'v1', CONFIG['feature'] + '; score=-VOL20', 'float'),))
    features = attach_features_to_observation_index(observations, VolatilitySource(), spec=feature_spec)
    outcomes = build_point_in_time_outcome_panel(observations, snapshot, horizons=(10,), spec=outcome_spec)
    dataset_spec = replace(POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1, name='exploratory_vol20_h10_dataset',
                           feature_panel_spec=feature_spec, outcome_panel_spec=outcome_spec)
    dataset = build_point_in_time_research_dataset(observations, features, outcomes, spec=dataset_spec)
    frame = dataset.evaluation_frame()
    assert len(frame) == len(sessions) * 100
    print(f'One fixed VOL20/H10 evaluation: {len(sessions)} sessions x 100 members', flush=True)
finally:
    for con in connections:
        con.close()
assert digest(DB) == CONFIG['input_sha256']
assert all(not Path(str(DB) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
assert 'core.database' not in sys.modules and 'vnstock' not in sys.modules
detail = frame.loc[:, ['session_date', 'symbol', 'market_row_available', 'availability', 'neg_vol20',
                      'neg_vol20__availability', 'target_session_10', 'stock_forward_return_10_pct', 'outcome_10__availability']].copy()
detail['vol20'] = -detail['neg_vol20']
detail['h10_return'] = detail['stock_forward_return_10_pct'] / 100
factor_ok = detail['neg_vol20__availability'].eq('AVAILABLE') & np.isfinite(detail['neg_vol20'].astype(float))
outcome_ok = detail['outcome_10__availability'].eq('AVAILABLE') & np.isfinite(detail['h10_return'].astype(float))
detail['pairwise_eligible'] = factor_ok & outcome_ok
detail['exclusion_reason'] = np.select([~factor_ok & outcome_ok, factor_ok & ~outcome_ok, ~factor_ok & ~outcome_ok],
                         ['FACTOR_UNAVAILABLE', 'H10_OUTCOME_UNAVAILABLE', 'FACTOR_AND_H10_UNAVAILABLE'], default='ELIGIBLE')
adapter_detail = pd.concat([f.assign(symbol=s) for s, f in frames.items()], ignore_index=True)
adapter_detail['session_date'] = adapter_detail['time'].dt.strftime('%Y-%m-%d')
detail = detail.merge(adapter_detail[['session_date', 'symbol', 'return_count_20', 'vol20_reason']],
                      on=['session_date', 'symbol'], how='left', validate='one_to_one', sort=False)
daily = []
targets = dict(zip(sessions, sessions[10:] + (None,) * 10))
for date, group in detail.groupby('session_date', sort=True):
    eligible = group.loc[group['pairwise_eligible']].sort_values('symbol', kind='stable')
    ic, reason = rank_ic(tuple(eligible['neg_vol20'].astype(float)), tuple(eligible['h10_return'].astype(float)),
                         CONFIG['minimum_cross_section_size'])
    daily.append({**binding, 'session_date': date, 'target_session': targets[date], 'cohort_count': len(group),
                  'vol20_available': int(group['neg_vol20__availability'].eq('AVAILABLE').sum()),
                  'h10_available': int(group['outcome_10__availability'].eq('AVAILABLE').sum()),
                  'eligible_pairs': len(eligible), 'rank_ic': ic, 'undefined_reason': reason})
daily = pd.DataFrame(daily)
assert len(daily) == len(sessions) and daily['eligible_pairs'].sum() == detail['pairwise_eligible'].sum()
defined = daily.loc[daily['rank_ic'].notna()]
ics = tuple(defined['rank_ic'].astype(float))
assert ics
annual = []
for year, group in daily.groupby(daily['session_date'].str[:4], sort=True):
    values = tuple(group['rank_ic'].dropna().astype(float))
    annual.append({**binding, 'year': year, 'first_session': group['session_date'].iloc[0], 'last_session': group['session_date'].iloc[-1],
                   'scheduled_dates': len(group), 'defined_ic_dates': len(values), 'eligible_pairs': int(group['eligible_pairs'].sum()),
                   'equal_date_mean_ic': _mean(values), 'positive_ic_share': sum(v > 0 for v in values) / len(values) if values else None})
missing = {name: {str(k): int(v) for k, v in detail[name].value_counts().sort_index().items()}
           for name in ('availability', 'neg_vol20__availability', 'outcome_10__availability', 'vol20_reason', 'exclusion_reason')}
missing['undefined_daily_reasons'] = {str(k): int(v) for k, v in daily['undefined_reason'].dropna().value_counts().items()}
summary = {**binding, 'status': 'COMPLETED', 'hypothesis': CONFIG['hypothesis'], 'cohort_identity': CONFIG['cohort_identity'],
    'cohort_count': 100, 'benchmark': benchmark, 'window': window, 'snapshot_id': snapshot.snapshot_id,
    'logical_fingerprint': snapshot.logical_content_fingerprint, 'stored_benchmark_sessions': len(sessions),
    'population_observations': len(detail), 'market_available_observations': int(detail['market_row_available'].sum()),
    'pairwise_eligible_observations': int(detail['pairwise_eligible'].sum()), 'defined_ic_dates': len(ics),
    'undefined_ic_dates': len(sessions) - len(ics), 'first_defined_ic_date': defined['session_date'].iloc[0],
    'last_defined_ic_date': defined['session_date'].iloc[-1], 'overall_equal_date_mean_rank_ic': _mean(ics),
    'positive_ic_dates': sum(value > 0 for value in ics), 'zero_ic_dates': sum(value == 0 for value in ics),
    'negative_ic_dates': sum(value < 0 for value in ics), 'positive_ic_share': sum(value > 0 for value in ics) / len(ics),
    'observation_coverage_fraction': float(detail['pairwise_eligible'].mean()), 'ic_coverage_fraction': len(ics) / len(sessions),
    'eligible_pairs_per_defined_date': {'minimum': int(defined['eligible_pairs'].min()), 'maximum': int(defined['eligible_pairs'].max())},
    'spec_fingerprints': {'feature': feature_spec.fingerprint, 'outcome': outcome_spec.fingerprint, 'dataset': dataset_spec.fingerprint},
    'missingness': missing, 'chronological_slices': annual, 'price_basis': 'UNKNOWN', 'qualified': False,
    'parameter_search': False, 'provider_requests': 0, 'input_unchanged': True,
    'limitations': ['Fixed current tracked ticker cohort applied retrospectively; survivorship, selection and ticker-identity limitations.',
       'Unknown provider, adjustment basis and corporate-action treatment; observed-basis close returns, not verified RAW or total returns.',
       'Current historical vintage; original publication times, revisions and provenance ledger unavailable.',
       'Stored VNINDEX sessions are not an independently verified official calendar; missing bars never filled.',
       'Overlapping H10 outcomes and cross-sectional/temporal dependence; no significance, alpha or profitability claims.',
       'Prior MOM20/ADX outcomes already exposed; descriptive study is not independent confirmation.',
       'VOL20 requires all 20 exact-session returns; its eligible pairs can differ from MOM20 even on the same input.',
       'Private data retention only; redistribution rights remain unverified.']}
OUT.mkdir(parents=True)

def write_json(name, value):
    with (OUT / name).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')

def write_csv(name, value):
    value.to_csv(OUT / name, index=False, float_format='%.17g', lineterminator='\n', na_rep='')

for name, value in reversed(tuple(binding.items())):
    detail.insert(0, name, value)
with (OUT / 'observations.csv.gz').open('xb') as raw:
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with io.TextIOWrapper(compressed, encoding='utf-8', newline='') as stream:
            detail.to_csv(stream, index=False, float_format='%.17g', lineterminator='\n', na_rep='')
write_csv('daily_ic.csv', daily)
write_csv('chronological_slices.csv', pd.DataFrame(annual))
write_json('summary.json', summary)
write_json('validation.json', validation)
write_json('code_manifest.json', code_manifest)
lines = ['# Exploratory VOL20–H10 research note', '', '**EXPLORATORY_ONLY / LEGACY_UNVERIFIED — STATUS: COMPLETED.**', '',
    'Predefined hypothesis: ' + CONFIG['hypothesis'], '',
    f"Input SHA256: `{CONFIG['input_sha256']}`; snapshot ID: `{snapshot.snapshot_id}`. Exact Cohort V1: experiment.json and the preserved MOM20 cohort manifest. All {len(sessions)} stored VNINDEX dates, {sessions[0]} through {sessions[-1]}, and all 100 fixed members are retained.", '',
    'Daily simple returns are close(t)/close(t−1)−1 on the exact stored benchmark index. VOL20 is sample standard deviation (ddof=1) of the twenty returns r(t−19)..r(t), requiring 21 finite positive closes with no filling. Score is −VOL20; a positive IC describes the predefined lower-volatility direction. H10 uses the unchanged exact-session outcome panel; observed-basis labels are not verified RAW returns.', '',
    'Generic existing observation/feature/outcome/dataset panels retain availability and missingness. Rank IC reuses the unchanged existing ascending average-tie ranking and Pearson primitives, minimum five eligible pairs, and constant/undefined rules. The built-in factor whitelist is unchanged; VOL20 is explicitly named. Defined daily ICs receive equal weight; positive frequency includes zero IC dates in the denominator. Calendar years were fixed before outcomes.', '',
    f"Mean Rank IC: **{summary['overall_equal_date_mean_rank_ic']:.10f}**. Positive IC frequency: **{summary['positive_ic_share']:.4%}**, {summary['positive_ic_dates']}/{len(ics)} defined dates; zero: {summary['zero_ic_dates']}.", '',
    f"Eligible observations: **{summary['pairwise_eligible_observations']:,}/{len(detail):,}** ({summary['observation_coverage_fraction']:.2%}). Defined IC dates: **{len(ics)}/{len(sessions)}** ({summary['ic_coverage_fraction']:.2%}), {summary['first_defined_ic_date']} through {summary['last_defined_ic_date']}. Undefined dates: {summary['undefined_ic_dates']}. Defined-date pair counts: {summary['eligible_pairs_per_defined_date']}.", '',
    '| Calendar year | All dates | Defined ICs | Mean IC | Positive share |', '|---|---:|---:|---:|---:|']
lines.extend(f"| {r['year']} | {r['scheduled_dates']} | {r['defined_ic_dates']} | {r['equal_date_mean_ic']:.8f} | {r['positive_ic_share']:.2%} |" for r in annual)
lines += ['', 'Focused synthetic checks passed: independently calculated sample standard deviation, missing intermediate bar, causal prefix/future perturbation, tied ranks/constant/minimum-size behavior, and exact H10 target without sliding over a missing stock close. validation.json retains expected and actual arithmetic values.', '',
    'No raw IC superiority comparison with MOM20 or ADX is made. Data and calendar match MOM20, but feature eligibility can differ; earlier exploratory exposure and dependence prohibit independent-confirmation claims.', '',
    'Limitations:', *['- ' + limit for limit in summary['limitations']], '', 'Reproduce with the recorded Python/pandas/NumPy runtime, after restoring both complete private studies as sibling directories:', '',
    '```powershell', '.\\.venv\\Scripts\\python.exe -B research/alpha_hypotheses/vol20_h10_exploratory_20261010/run_study.py --output research/alpha_hypotheses/vol20_h10_exploratory_20261010/replay_outputs', '```', '',
    'Replay rejects altered input/cohort/engine files and existing output directories, reads only the frozen MOM20 SQLite snapshot, and prohibits networking. No canonical store, strategy, scanner or Daily code is used. code_manifest.json records runner/config/engine hashes and versions; SHA256.json records deterministic result identities. observations.csv.gz retains every expected row, score, label, window count, availability and reasons.', '',
    'Conclusion: descriptive historical association only, EXPLORATORY_ONLY / LEGACY_UNVERIFIED. No parameter search, significance, alpha, profitability, provider collection or controlled experiment.']
(OUT / 'RESEARCH_NOTE.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
hashes = {p.relative_to(ROOT).as_posix(): digest(p) for p in ROOT.rglob('*')
          if p.is_file() and p.name != 'SHA256.json' and not p.is_relative_to(ROOT / 'replay_outputs')}
write_json('SHA256.json', {'classification': LABEL, 'files': dict(sorted(hashes.items()))})
print(json.dumps({key: summary[key] for key in ('status', 'overall_equal_date_mean_rank_ic', 'positive_ic_dates',
      'positive_ic_share', 'pairwise_eligible_observations', 'defined_ic_dates', 'undefined_ic_dates')}), flush=True)
