from __future__ import annotations
import inspect, os, sys, argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np, pandas as pd
import types, sys
if "vnstock" not in sys.modules:
    m=types.ModuleType("vnstock")
    class _V: pass
    m.Vnstock=_V
    sys.modules["vnstock"]=m

from backtesting.engine import build_exit_model, generate_candidate_trades, run_backtest, BacktestConfig
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from backtesting.position_sizers import PositionSizer, PositionSizingContext

FEATURES=('signal_score','relative_strength','adx','volume_ratio')

def num(x,d=None):
    try:
        x=float(x); return x if np.isfinite(x) else d
    except: return d

def health(path):
    d=pd.read_csv(path); d['time']=pd.to_datetime(d['date']).dt.normalize(); d['breadth_ema50_pct']=d['breadth50']; d['breadth_ema50_change_10d']=d['breadth50_chg_10d']; return d.drop_duplicates('time').set_index('time').sort_index()

def state(date_, regime, h):
    r=h.loc[pd.Timestamp(date_).normalize()] if pd.Timestamp(date_).normalize() in h.index else None
    regime=str(regime).upper()
    if regime=='BEAR': return 'BEAR'
    if r is None: return 'NEUTRAL'
    b=num(r.get('breadth_ema50_pct')); c=num(r.get('breadth_ema50_change_10d'))
    if regime=='BULL':
        if b is not None and c is not None and b<50 and c<0: return 'DIVERGENT_BULL'
        if b is not None and c is not None and b>=70 and c>=0: return 'HEALTHY_BULL'
        return 'FRAGILE_BULL'
    if regime=='SIDEWAY' and b is not None and c is not None and b>=60 and c>0: return 'RECOVERY'
    return 'NEUTRAL'

@dataclass(frozen=True)
class QModel:
    refs: dict[str,pd.Series]
    @classmethod
    def fit(cls, ts):
        rows=[{f:getattr(t, {'signal_score':'signal_score','relative_strength':'relative_strength','adx':'adx','volume_ratio':'volume_ratio'}[f],None) for f in FEATURES} for t in ts]
        df=pd.DataFrame(rows); return cls({f:pd.to_numeric(df.get(f,pd.Series()),errors='coerce').dropna() for f in FEATURES})
    def score(self,t):
        vals=[]
        for f in FEATURES:
            x=num(getattr(t, f, None)); ref=self.refs[f]
            vals.append(.5 if x is None or ref.empty else float((ref<=x).mean()))
        return float(np.mean(vals))

class GateSizer(PositionSizer):
    def __init__(self, base, qmodel, h, threshold, fragile_mult): self.base,self.q,self.h,self.threshold,self.fragile=base,qmodel,h,threshold,fragile_mult
    @property
    def name(self): return f'{self.base.name}_state_exposure_q{self.threshold:.2f}_f{self.fragile:.2f}'
    def calculate_quantity(self, context: PositionSizingContext):
        t=context.candidate; q=self.q.score(t); st=state(t.entry_date,t.market_regime,self.h)
        if st=='DIVERGENT_BULL' or q<self.threshold: return 0
        q0=self.base.calculate_quantity(context)
        mult=self.fragile if st=='FRAGILE_BULL' else 1.0
        qty=int(q0*mult)//context.lot_size*context.lot_size
        return max(0,qty)

def parity(p):
    vals={k:getattr(p,k) for k in ('initial_cash','position_sizer','risk_per_trade_pct','atr_stop_multiplier','fixed_fraction_pct','lot_size','commission_rate','slippage_bps','maximum_position_pct','maximum_gross_exposure_pct','maximum_open_positions','maximum_daily_loss_pct','minimum_cash_buffer_pct','maximum_order_adtv20_pct','sell_tax_rate')}
    params=inspect.signature(BacktestPaperParityConfig).parameters
    return BacktestPaperParityConfig(**{k:v for k,v in vals.items() if k in params})

def kwargs(p,pol,par,symbols,entry,db,sizer=None):
    return dict(symbols=symbols,max_holding_days=pol.maximum_holding_days,entry_model=entry,
      exit_model=build_exit_model(name='atr',stop_atr_multiplier=pol.stop_atr_multiplier,target_atr_multiplier=pol.target_atr_multiplier,break_even_trigger=pol.target_atr_multiplier,trailing_atr_multiplier=pol.stop_atr_multiplier),
      ranking_method='signal_score',position_sizer=sizer or par.build_position_sizer(),buy_commission_pct=par.commission_pct,sell_commission_pct=par.commission_pct,sell_tax_pct=par.sell_tax_pct,buy_slippage_pct=par.slippage_pct,sell_slippage_pct=par.slippage_pct,max_positions=par.maximum_open_positions,lot_size=par.lot_size,max_new_positions_per_day=p.maximum_orders_per_scan,maximum_gross_exposure_pct=par.maximum_gross_exposure_pct,minimum_cash_buffer_pct=par.minimum_cash_buffer_pct,db_path=db)

def candidates(symbols, kw, a,b):
    params=inspect.signature(BacktestConfig).parameters; cfg=BacktestConfig(**{k:v for k,v in kw.items() if k in params}); out=[]
    for s in symbols:
      try: out += generate_candidate_trades(symbol=s,config=cfg,db_path=kw['db_path'],warmup_bars=60,verbose=False,entry_model=kw['entry_model'],exit_model=kw['exit_model'],start_date=str(pd.Timestamp(a).date()),end_date=str(pd.Timestamp(b).date()))
      except Exception as e: print('[WARN]',s,e)
    return out

def summ(ts):
    r=np.array([num(getattr(t,'net_return_pct',0),0) for t in ts if getattr(t,'is_closed',False)],float); gains=r[r>0].sum(); losses=-r[r<0].sum()
    return dict(trades=len(r),win_rate_pct=(r>0).mean()*100 if len(r) else 0,avg_trade_pct=r.mean() if len(r) else 0,median_trade_pct=np.median(r) if len(r) else 0,profit_factor=gains/losses if losses else np.inf)

def main():
 p=argparse.ArgumentParser(); p.add_argument('--market-health',required=True); p.add_argument('--db-path',required=True); p.add_argument('--out',default='research_results/exposure_policy_wfo'); p.add_argument('--start-date',default='2018-08-07'); p.add_argument('--end-date',default='2026-08-21'); a=p.parse_args(); Path(a.out).mkdir(parents=True,exist_ok=True)
 h=health(a.market_health); paper=PaperExecutionConfig.from_env(); pol=TradingPolicy.from_env(); par=parity(paper)
 import sqlite3
 con=sqlite3.connect(a.db_path); symbols=sorted(pd.read_sql("select distinct symbol from prices where upper(symbol) <> 'VNINDEX'",con)['symbol'].tolist()); con.close(); print('symbols',len(symbols))
 folds=build_walk_forward_folds(WalkForwardConfig(a.start_date,a.end_date,24,6,6))
 policies=[('baseline',None,None),('q65',.65,1.0),('q70',.70,1.0),('q65_f05',.65,.5),('q70_f05',.70,.5),('q65_f025',.65,.25),('q70_f025',.70,.25)]
 # independent capital chain per policy
 caps={x:par.initial_cash for x,_,_ in policies}; rows=[]; trades_rows=[]
 for f in folds:
  print('FOLD',f.fold, f.train_start.date(),f.test_start.date())
  train_kw=kwargs(paper,pol,par,symbols,pol.build_entry_model(),a.db_path); tr=candidates(symbols,train_kw,f.train_start,f.train_end); qm=QModel.fit(tr); print('train candidates',len(tr))
  for name,thr,fm in policies:
   base=pol.build_entry_model(); sizer=par.build_position_sizer()
   if thr is not None: sizer=GateSizer(sizer,qm,h,thr,fm)
   kw=kwargs(paper,pol,par,symbols,base,a.db_path,sizer)
   ts,met,eq=run_backtest(**kw,start_date=str(f.test_start.date()),end_date=str(f.test_end.date()),initial_capital=caps[name],verbose=False)
   final=float(met.get('final_equity',caps[name])); caps[name]=final; sm=summ(ts); ret=float(met.get('total_return_pct',0))
   rows.append(dict(fold=f.fold,test_start=f.test_start.date(),test_end=f.test_end.date(),policy=name,fragile_multiplier=fm,threshold=thr,starting_capital=final/(1+ret/100) if ret>-100 else np.nan,final_equity=final,fold_return_pct=ret,**sm,train_candidates=len(tr)))
   for t in ts:
    if getattr(t,'is_closed',False): trades_rows.append({**t.to_dict(),'fold':f.fold,'policy':name,'threshold':thr,'fragile_multiplier':fm,'market_state':state(t.entry_date,t.market_regime,h),'quality':qm.score(t) if thr is not None else np.nan})
  print(' done')
 df=pd.DataFrame(rows); td=pd.DataFrame(trades_rows); df.to_csv(Path(a.out)/'exposure_policy_fold_summary.csv',index=False); td.to_csv(Path(a.out)/'exposure_policy_trade_level.csv',index=False)
 summary=[]
 for name,g in df.groupby('policy'):
  x=g.fold_return_pct.values; comp=(np.prod(1+x/100)-1)*100; eq=np.cumprod(1+x/100); dd=(eq/np.maximum.accumulate(eq)-1).min()*100
  summary.append(dict(policy=name,folds=len(g),profitable_folds=int((x>0).sum()),mean_fold_return_pct=x.mean(),median_fold_return_pct=np.median(x),compounded_oos_return_pct=comp,fold_max_drawdown_pct=dd,worst_fold_pct=x.min(),best_fold_pct=x.max(),total_test_trades=int(g.trades.sum()),avg_profit_factor=g.profit_factor.replace([np.inf],np.nan).mean()))
 pd.DataFrame(summary).sort_values('policy').to_csv(Path(a.out)/'exposure_policy_summary.csv',index=False); print(pd.DataFrame(summary).to_string(index=False))
if __name__=='__main__': main()
