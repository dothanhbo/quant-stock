from pathlib import Path
import pandas as pd
import numpy as np

INPUT = Path('research_results/bull_entry_regime_audit_v2/selected_case_feature_enriched.csv')
OUTPUT = Path('research_results/alpha_interaction_attribution')
OUTPUT.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(INPUT)
df = df[df['case_id'].eq('fixed_atr_2_4')].copy()

def stats(g):
    r = pd.to_numeric(g['net_return_pct'], errors='coerce').dropna()
    wins = r[r > 0].sum()
    losses = -r[r <= 0].sum()
    return pd.Series({'trades': len(r), 'win_rate_pct': (r > 0).mean()*100,
                      'expectancy_pct': r.mean(), 'median_return_pct': r.median(),
                      'profit_factor': wins/losses if losses > 0 else np.inf})

pairs = [
 ('market_regime','market_return20_bucket'), ('market_regime','breadth_ema50_bucket'),
 ('signal_adx_bucket','signal_volume_bucket'), ('signal_adx_bucket','signal_distance_ema20_bucket'),
 ('signal_score_bucket','signal_volume_bucket'), ('signal_score_bucket','signal_distance_ema20_bucket'),
 ('signal_rsi_bucket','signal_volume_bucket'), ('signal_rsi_bucket','signal_distance_ema20_bucket')]

frames=[]
for a,b in pairs:
    x=df.groupby([a,b],observed=True).apply(stats,include_groups=False).reset_index()
    x['dimension_a']=a; x['dimension_b']=b
    x['combination']=x[a].astype(str)+' × '+x[b].astype(str)
    frames.append(x[x['trades']>=15][['dimension_a','dimension_b','combination','trades','win_rate_pct','expectancy_pct','median_return_pct','profit_factor']])

interactions=pd.concat(frames,ignore_index=True).sort_values(['expectancy_pct','trades'],ascending=[False,False])
interactions.to_csv(OUTPUT/'interaction_metrics.csv',index=False)

df.groupby(['market_regime','exit_reason'],observed=True).apply(stats,include_groups=False).reset_index().to_csv(OUTPUT/'exit_attribution.csv',index=False)
df.groupby('signal_year',observed=True).apply(stats,include_groups=False).reset_index().to_csv(OUTPUT/'year_attribution.csv',index=False)

readme='''# Alpha Interaction + Attribution Audit\n\nResearch-only. No production strategy/config changed.\n\nSource: research_results/bull_entry_regime_audit_v2/selected_case_feature_enriched.csv (case_id=fixed_atr_2_4).\n\nFiles:\n- interaction_metrics.csv: feature combinations with >=15 trades\n- exit_attribution.csv: market regime x exit reason\n- year_attribution.csv: year-level attribution\n\nDo not promote a bucket to a production filter based on this report alone; validate it with WFO/OOS and sample-size/robustness gates.\n'''
(OUTPUT/'README.md').write_text(readme,encoding='utf-8')
print(interactions.head(15).to_string(index=False))
