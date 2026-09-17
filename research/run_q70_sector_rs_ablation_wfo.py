from __future__ import annotations
"""Research-only chained-OOS ablation: V2 Q70 vs V2 Q70 + Sector RS 60D."""
import argparse
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy, apply_strategy_config
from core.sector import fetch_sector_mapping
from core.universe import get_vn100_symbols
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS
from strategy.relative_strength_v2 import DEFAULT_SECTOR_RS_PERIOD, calculate_sector_relative_strength

QUALITY_FEATURES=("signal_score","relative_strength","adx","volume_ratio")
SECTOR_RS_PERIOD=DEFAULT_SECTOR_RS_PERIOD

def _num(value: Any, default: float=np.nan)->float:
    try: x=float(value)
    except (TypeError,ValueError): return default
    return x if np.isfinite(x) else default

@dataclass(frozen=True)
class FrozenQuality:
    refs: dict[str,np.ndarray]
    @classmethod
    def fit(cls,candidates:list[Any])->"FrozenQuality":
        refs={}
        for feature in QUALITY_FEATURES:
            vals=[_num(getattr(c,feature,np.nan)) for c in candidates]
            refs[feature]=np.sort(np.asarray([v for v in vals if np.isfinite(v)],dtype=float))
        return cls(refs)
    def score(self,candidate:Any)->float:
        scores=[]
        for feature in QUALITY_FEATURES:
            value=_num(getattr(candidate,feature,np.nan)); ref=self.refs[feature]
            scores.append(0.5 if not np.isfinite(value) or len(ref)==0 else float(np.searchsorted(ref,value,side="right")/len(ref)))
        return float(np.mean(scores))

def fit_sector_rs_threshold(values:list[float],percentile:float)->float:
    if not 0.0 < percentile < 1.0: raise ValueError("percentile must be between 0 and 1")
    clean=np.asarray([x for x in values if np.isfinite(x)],dtype=float)
    if clean.size==0: raise ValueError("No finite Sector RS observations in TRAIN.")
    return float(np.quantile(clean,percentile))

def enrich_sector_rs(candidates:list[Any],mapping:dict[str,str],universe:list[str])->pd.DataFrame:
    rows=[]
    for c in candidates:
        result=calculate_sector_relative_strength(
            str(getattr(c,"symbol","")).upper(),mapping,
            period=SECTOR_RS_PERIOD,
            as_of_date=getattr(c,"signal_date",None),
            universe_symbols=universe,
        )
        rows.append({"candidate":c,"sector_rs_60d":_num(result.get("relative_strength"))})
    return pd.DataFrame(rows)

def build_candidates(symbols:list[str],db_path:str,policy:TradingPolicy,paper:PaperExecutionConfig,start_date:str,end_date:str)->list[Any]:
    config=BacktestConfig(
        max_holding_days=policy.maximum_holding_days, initial_capital=paper.initial_cash,
        buy_commission_pct=paper.commission_rate*100, sell_commission_pct=paper.commission_rate*100,
        sell_tax_pct=paper.sell_tax_rate*100, buy_slippage_pct=paper.slippage_bps/100,
        sell_slippage_pct=paper.slippage_bps/100, ranking_method="signal_score")
    entry_model=policy.build_entry_model()
    exit_model=build_exit_model(name="atr",stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        break_even_trigger=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier)
    out=[]
    for symbol in symbols:
        out.extend(generate_candidate_trades(symbol=symbol,config=config,db_path=db_path,
            warmup_bars=60,verbose=False,start_date=start_date,end_date=end_date,
            entry_model=entry_model,exit_model=exit_model))
    return out

def simulate(candidates:list[Any],paper:PaperExecutionConfig,parity:BacktestPaperParityConfig,initial_capital:float):
    costs=TransactionCostConfig(buy_commission_pct=parity.commission_pct,
        sell_commission_pct=parity.commission_pct,sell_tax_pct=parity.sell_tax_pct,
        buy_slippage_pct=parity.slippage_pct,sell_slippage_pct=parity.slippage_pct)
    sim=PortfolioSimulator(initial_cash=initial_capital,position_size_pct=paper.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),ranking_method="signal_score",
        transaction_cost_config=costs,max_positions=paper.maximum_open_positions,lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct)
    result=sim.simulate(copy.deepcopy(candidates))
    metrics=calculate_portfolio_metrics(result.equity_curve,final_equity=float(result.final_equity))
    return result.executed_trades,metrics

def select_fold(train:list[Any],test:list[Any],quality_threshold:float,sector_percentile:float,mapping:dict[str,str],universe:list[str]):
    quality=FrozenQuality.fit(train)
    train_q70=[c for c in train if quality.score(c)>=quality_threshold]
    test_q70=[c for c in test if quality.score(c)>=quality_threshold]
    train_rs=enrich_sector_rs(train_q70,mapping,universe)
    test_rs=enrich_sector_rs(test_q70,mapping,universe)
    threshold=fit_sector_rs_threshold(train_rs["sector_rs_60d"].tolist(),sector_percentile)
    selected=[row["candidate"] for _,row in test_rs.iterrows() if np.isfinite(row["sector_rs_60d"]) and row["sector_rs_60d"]>=threshold]
    return test_q70,selected,threshold,int(train_rs["sector_rs_60d"].notna().sum()),int(test_rs["sector_rs_60d"].notna().sum())

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db-path",default="data/market.db"); p.add_argument("--symbols",nargs="*",default=None)
    p.add_argument("--start",default="2018-08-07"); p.add_argument("--end",default="2026-08-21")
    p.add_argument("--train-months",type=int,default=24); p.add_argument("--test-months",type=int,default=6); p.add_argument("--step-months",type=int,default=6)
    p.add_argument("--quality-threshold",type=float,default=0.70); p.add_argument("--sector-percentile",type=float,default=0.80)
    p.add_argument("--output",default="research_results/q70_sector_rs_ablation_holdout20")
    args=p.parse_args()
    from config.strategy_config import V2_Q70_ATR45_9
    apply_strategy_config(V2_Q70_ATR45_9)
    paper=PaperExecutionConfig.from_env(); policy=TradingPolicy.from_env()
    parity=BacktestPaperParityConfig.from_paper_config(paper,sell_tax_rate=paper.sell_tax_rate)
    symbols=list(HOLDOUT20_SYMBOLS) if args.symbols is None else sorted({s.strip().upper() for s in args.symbols if s.strip()})
    folds=build_walk_forward_folds(WalkForwardConfig(start_date=args.start,end_date=args.end,train_months=args.train_months,test_months=args.test_months,step_months=args.step_months))
    mapping=fetch_sector_mapping(); universe=list(get_vn100_symbols())
    output=Path(args.output); output.mkdir(parents=True,exist_ok=True)
    base_cap=sector_cap=float(parity.initial_cash); fold_rows=[]; trade_rows=[]
    print(f"V2={V2_Q70_ATR45_9.name} | symbols={len(symbols)} | sector_universe={len(universe)} | folds={len(folds)}")
    print(f"Sector RS={SECTOR_RS_PERIOD}D | threshold percentile={args.sector_percentile:.0%} | TRAIN-only")
    for fold in folds:
        train=build_candidates(symbols,args.db_path,policy,paper,str(fold.train_start.date()),str(fold.train_end.date()))
        test=build_candidates(symbols,args.db_path,policy,paper,str(fold.test_start.date()),str(fold.test_end.date()))
        base,sector,threshold,train_rs_n,test_rs_n=select_fold(train,test,args.quality_threshold,args.sector_percentile,mapping,universe)
        bt,bm=simulate(base,paper,parity,base_cap); st,sm=simulate(sector,paper,parity,sector_cap)
        bf=float(bm.get("final_equity",base_cap)); sf=float(sm.get("final_equity",sector_cap))
        br=(bf/base_cap-1)*100; sr=(sf/sector_cap-1)*100
        fold_rows.append({"fold":fold.fold,"train_start":fold.train_start.date(),"train_end":fold.train_end.date(),
            "test_start":fold.test_start.date(),"test_end":fold.test_end.date(),"train_candidates":len(train),"test_candidates":len(test),
            "baseline_q70_candidates":len(base),"sector_rs_candidates":len(sector),"train_sector_rs_available":train_rs_n,
            "test_sector_rs_available":test_rs_n,"sector_rs_threshold":threshold,"baseline_return_pct":br,"sector_return_pct":sr,
            "delta_pp":sr-br,"baseline_trades":len(bt),"sector_trades":len(st),
            "baseline_sharpe":float(bm.get("sharpe_ratio",0.0)),"sector_sharpe":float(sm.get("sharpe_ratio",0.0)),
            "baseline_max_drawdown_pct":float(bm.get("max_drawdown_pct",0.0)),"sector_max_drawdown_pct":float(sm.get("max_drawdown_pct",0.0))})
        for name,trades in (("baseline_q70",bt),("q70_sector_rs",st)):
            for t in trades:
                trade_rows.append({"policy":name,"fold":fold.fold,"symbol":getattr(t,"symbol",""),
                    "signal_date":getattr(t,"signal_date",""),"entry_date":getattr(t,"entry_date",""),
                    "net_return_pct":getattr(t,"net_return_pct",np.nan)})
        base_cap=bf; sector_cap=sf
        print(f"FOLD {fold.fold}: baseline={br:+.2f}% | sectorRS={sr:+.2f}% | delta={sr-br:+.2f}pp | candidates {len(base)}->{len(sector)}")
    fd=pd.DataFrame(fold_rows); td=pd.DataFrame(trade_rows)
    fd.to_csv(output/"fold_summary.csv",index=False,encoding="utf-8-sig"); td.to_csv(output/"trade_level.csv",index=False,encoding="utf-8-sig")
    def summary(ret_col,tr_col):
        r=fd[ret_col].to_numpy(float)
        return {"folds":len(r),"profitable_folds":int((r>0).sum()),"mean_fold_return_pct":float(r.mean()),
            "median_fold_return_pct":float(np.median(r)),"compounded_oos_return_pct":float((np.prod(1+r/100)-1)*100),
            "total_trades":int(fd[tr_col].sum()),"worst_fold_pct":float(r.min()),"best_fold_pct":float(r.max())}
    sd=pd.DataFrame([{"policy":"baseline_q70",**summary("baseline_return_pct","baseline_trades")},
                     {"policy":"q70_sector_rs",**summary("sector_return_pct","sector_trades")}])
    sd.to_csv(output/"summary.csv",index=False,encoding="utf-8-sig")
    print("\n=== V2 Q70 SECTOR RS ABLATION ==="); print(sd.to_string(index=False)); print(f"\nOutput: {output.resolve()}")

if __name__=="__main__": main()
