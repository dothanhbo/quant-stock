from __future__ import annotations

"""Publish Phase 10C outcomes for frozen Phase 10B counterfactual policies."""

import argparse
import csv
from dataclasses import fields
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import MappingProxyType
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio import (
    CASH_RETURN_ASSUMPTION,
    FrozenForwardOutcome,
    RiskPolicy,
    RiskPolicyPortfolio,
    RiskPolicyPosition,
    RiskPolicyState,
    RiskPolicyOutcomeResult,
    evaluate_risk_policy_outcomes,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE10B_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_policy_2018-08-07_2026-09-17"
DEFAULT_PHASE65_ROOT = PROJECT_ROOT / "research_results/quantlab_point_in_time_outcome_evidence_2018-08-07_2026-09-17"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_risk_policy_outcome_2018-08-07_2026-09-17"
EXPECTED_PHASE10B_MANIFEST_SHA256 = "9cb56ee10695bdba21e0c2d5c370ade137c918755064902253a2fbf3af39ac7b"
EXPECTED_PHASE10B_RESULT = "d0c0ca9db3473f6eeb61d6eb84b8a9683003ca8ae3513bc183188549e13d65fe"
EXPECTED_PHASE10B_SPEC = "9a0634ce2c5c604ddcbe64c49c96a0674ac00b8ed17ca4168426b7775b637414"
EXPECTED_POLICY_FINGERPRINTS = {
    "NO_RISK_POLICY": "0384b8e21699e02f274a4bb60858097bcde44eca9cd2f6ae9ac5dcba36525c8d",
    "VOLATILITY_SCALING": "354067781f3906791f34d9b20329bac82907d4a18949a0108b04ad7f7e871aff",
}
EXPECTED_PHASE65_MANIFEST_SHA256 = "46bc2f5ff3c99ea14b9798f0c3a116a258274a9ae50fcd95ac164b01845e0e86"
EXPECTED_PHASE65_OUTCOMES_SHA256 = "2bea6502a27cf7caccfbaa174c7be0b708bc7a89e175c26fef8f18293e174712"
FILES = (
    "portfolio_risk_policy_outcome_by_date.csv", "portfolio_risk_policy_outcome_summary.csv",
    "portfolio_risk_policy_outcome_by_block.csv", "portfolio_risk_policy_outcome_contrasts.csv",
    "portfolio_risk_policy_cost_sensitivity.csv", "portfolio_risk_policy_outcome_manifest.json",
    "portfolio_risk_policy_outcome_report.md",
)


def _file_hash(path: Path) -> str:
    digest=sha256()
    with path.open("rb") as handle:
        while chunk:=handle.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, label: str) -> None:
    actual=_file_hash(path)
    if actual != expected: raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _rows(items: tuple[Any,...]) -> list[dict[str,Any]]:
    return [{field.name:(getattr(item,field.name).value if isinstance(getattr(item,field.name),Enum) else "" if getattr(item,field.name) is None else getattr(item,field.name)) for field in fields(item)} for item in items]


def _write_csv(path:Path,rows:list[dict[str,Any]])->None:
    if not rows: raise ValueError(f"cannot write empty artifact: {path.name}")
    with path.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=tuple(rows[0]),lineterminator="\n");writer.writeheader();writer.writerows(rows)


_INT_POLICY={"requested_budget","selected_count"}
_FLOAT_POLICY={"original_gross_weight","transformed_gross_weight","transformed_cash_weight","exposure_multiplier","exposure_reduction","transformation_turnover","max_position_weight","herfindahl_concentration"}
_OPTIONAL_FLOAT_POLICY={"effective_n","annualized_volatility_before","annualized_volatility_after","daily_variance_before","daily_variance_after","mean_pairwise_correlation","maximum_component_risk_share_before","maximum_component_risk_share_after","effective_risk_contributors_before","effective_risk_contributors_after","component_contribution_share_sum"}


def _load_phase10b(root:Path)->tuple[tuple[RiskPolicyPortfolio,...],dict[str,Any],str]:
    manifest_path=root/"portfolio_risk_policy_manifest.json"; _require_hash(manifest_path,EXPECTED_PHASE10B_MANIFEST_SHA256,"Phase 10B manifest")
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("result_identity")!=EXPECTED_PHASE10B_RESULT or manifest.get("specification_fingerprint")!=EXPECTED_PHASE10B_SPEC: raise ValueError("Phase 10B result/specification provenance mismatch")
    policies={item["name"]:item for item in manifest.get("policies",())}
    if {name:item.get("fingerprint") for name,item in policies.items()}!=EXPECTED_POLICY_FINGERPRINTS or policies["VOLATILITY_SCALING"].get("target_annualized_volatility")!=0.2: raise ValueError("Phase 10B frozen policy provenance mismatch")
    for name,metadata in manifest["artifacts"].items():
        if name in {"portfolio_risk_policy_by_date.csv","portfolio_risk_policy_positions.csv"}: _require_hash(root/name,metadata["sha256"],f"Phase 10B {name}")
    position_index:dict[str,list[RiskPolicyPosition]]={}
    with (root/"portfolio_risk_policy_positions.csv").open(encoding="utf-8",newline="") as handle:
        for row in csv.DictReader(handle):
            position_index.setdefault(row["portfolio_identity"],[]).append(RiskPolicyPosition(row["symbol"],float(row["original_weight"]),float(row["transformed_weight"]),row["source_position_identity"],row["position_identity"]))
    portfolios=[]
    with (root/"portfolio_risk_policy_by_date.csv").open(encoding="utf-8",newline="") as handle:
        for row in csv.DictReader(handle):
            values=[]
            for field in fields(RiskPolicyPortfolio):
                value=row.get(field.name,"")
                if field.name=="policy": values.append(RiskPolicy(value))
                elif field.name=="policy_state": values.append(RiskPolicyState(value))
                elif field.name=="positions": values.append(tuple(sorted(position_index.get(row["identity"],()),key=lambda item:item.symbol)))
                elif field.name in _INT_POLICY: values.append(int(value))
                elif field.name in _FLOAT_POLICY: values.append(float(value))
                elif field.name in _OPTIONAL_FLOAT_POLICY: values.append(None if value=="" else float(value))
                else: values.append(value)
            portfolios.append(RiskPolicyPortfolio(*values))
    if len(portfolios)!=int(manifest["artifacts"]["portfolio_risk_policy_by_date.csv"]["rows"]): raise ValueError("Phase 10B portfolio row count mismatch")
    return tuple(portfolios),manifest,_file_hash(manifest_path)


def _load_phase65(root:Path)->tuple[tuple[FrozenForwardOutcome,...],dict[str,Any],str]:
    manifest_path=root/"point_in_time_forward_outcomes_manifest.json"; data_path=root/"point_in_time_forward_outcomes.csv"
    _require_hash(manifest_path,EXPECTED_PHASE65_MANIFEST_SHA256,"Phase 6.5 manifest"); _require_hash(data_path,EXPECTED_PHASE65_OUTCOMES_SHA256,"Phase 6.5 outcome artifact")
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("horizons")!=[5,10,20] or manifest.get("benchmark_symbol")!="VNINDEX" or manifest.get("artifact",{}).get("sha256")!=EXPECTED_PHASE65_OUTCOMES_SHA256: raise ValueError("Phase 6.5 outcome provenance mismatch")
    outcomes=[]
    with data_path.open(encoding="utf-8",newline="") as handle:
        for row in csv.DictReader(handle):
            outcomes.append(FrozenForwardOutcome(
                row["session_date"],row["symbol"].strip().upper(),row["benchmark_symbol"],
                MappingProxyType({h:row[f"target_session_{h}"] or None for h in (5,10,20)}),
                MappingProxyType({h:(None if row[f"stock_forward_return_{h}_pct"]=="" else float(row[f"stock_forward_return_{h}_pct"])) for h in (5,10,20)}),
                MappingProxyType({h:(None if row[f"benchmark_forward_return_{h}_pct"]=="" else float(row[f"benchmark_forward_return_{h}_pct"])) for h in (5,10,20)}),
                MappingProxyType({h:(None if row[f"excess_forward_return_{h}_pct_points"]=="" else float(row[f"excess_forward_return_{h}_pct_points"])) for h in (5,10,20)}),
                MappingProxyType({h:row[f"outcome_{h}__availability"] for h in (5,10,20)}),
            ))
    if len(outcomes)!=int(manifest["row_count"]): raise ValueError("Phase 6.5 outcome row count mismatch")
    return tuple(outcomes),manifest,_file_hash(manifest_path)


def _report(result:RiskPolicyOutcomeResult)->str:
    lines=["# Phase 10C Frozen Risk Policy Outcome Evaluation","","Overlapping forward observations only; no executable path or policy decision.","",f"Cash return assumption: `{CASH_RETURN_ASSUMPTION}`.","","| Policy | Budget | Horizon | Evaluable | Mean portfolio % | Mean benchmark contribution % | Mean excess pp |","|---|---:|---:|---:|---:|---:|---:|"]
    shown=lambda value:"—" if value is None else f"{value:.8f}"
    for item in result.summaries:
        if item.subset=="ALL": lines.append(f"| {item.policy.value} | {item.requested_budget} | {item.horizon_sessions} | {item.evaluable_dates} | {shown(item.mean_portfolio_return_pct)} | {shown(item.mean_benchmark_contribution_pct)} | {shown(item.mean_excess_return_pct_points)} |")
    lines.extend(("","Path metrics: `NOT_APPLICABLE` — `OVERLAPPING_FORWARD_HORIZONS`.","","## Limitations",""));lines.extend(f"- {item}" for item in result.limitations);lines.append("")
    return "\n".join(lines)


def run_risk_policy_outcome(*,phase10b_root:str|Path=DEFAULT_PHASE10B_ROOT,phase65_root:str|Path=DEFAULT_PHASE65_ROOT,output_root:str|Path=DEFAULT_OUTPUT_ROOT)->RiskPolicyOutcomeResult:
    phase10b,phase65,output=Path(phase10b_root).resolve(),Path(phase65_root).resolve(),Path(output_root).resolve()
    if output.exists(): raise FileExistsError(f"output directory already exists: {output}")
    if output in {PROJECT_ROOT,PROJECT_ROOT/"research_results",phase10b,phase65}: raise ValueError("output root must be a dedicated Phase 10C directory")
    portfolios,manifest10b,hash10b=_load_phase10b(phase10b);outcomes,manifest65,hash65=_load_phase65(phase65)
    sources={"phase10b_manifest_sha256":hash10b,"phase10b_result_identity":manifest10b["result_identity"],"phase10b_specification_fingerprint":manifest10b["specification_fingerprint"],"phase65_manifest_sha256":hash65,"phase65_outcome_artifact_sha256":manifest65["artifact"]["sha256"],"phase65_observation_identity":manifest65["observation_index"]["identity"],"phase65_outcome_panel_identity":manifest65["outcome_panel"]["identity"]}
    result=evaluate_risk_policy_outcomes(portfolios,outcomes,source_identities=sources)
    rows={FILES[0]:_rows(result.observations),FILES[1]:_rows(result.summaries),FILES[2]:_rows(result.block_summaries),FILES[3]:_rows(result.contrasts),FILES[4]:_rows(result.cost_sensitivity)}
    output.parent.mkdir(parents=True,exist_ok=True);temporary=Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-",dir=output.parent))
    try:
        for name,values in rows.items(): _write_csv(temporary/name,values)
        (temporary/FILES[6]).write_text(_report(result),encoding="utf-8",newline="\n")
        artifacts={name:{"rows":len(values),"sha256":_file_hash(temporary/name)} for name,values in rows.items()};artifacts[FILES[6]]={"rows":1,"sha256":_file_hash(temporary/FILES[6])}
        manifest={"contract":result.contract_name,"version":result.contract_version,"specification_fingerprint":result.specification_fingerprint,"result_identity":result.identity,"source_identities":dict(result.source_identities),"policy_fingerprints":EXPECTED_POLICY_FINGERPRINTS,"volatility_target":0.20,"horizons":[5,10,20],"benchmark":"VNINDEX","cash_return_assumption":CASH_RETURN_ASSUMPTION,"portfolio_return_formula":"sum(transformed_weight_i * stock_forward_return_i_pct)","benchmark_formula":"headline VNINDEX return retained separately; portfolio benchmark contribution = risky_gross_weight * headline VNINDEX return","excess_formula":"portfolio return - portfolio benchmark contribution","availability":"all positive-weight constituents and benchmark required; no renormalization; zero-weight constituents inactive","path_metric_status":"NOT_APPLICABLE","path_metric_reason":"OVERLAPPING_FORWARD_HORIZONS","cost_grid_bps":[0,10,25,50],"cost_semantics":"Phase 10B transformation_turnover only; bps/100 percentage-point deduction; non-compounded","temporal_blocks":[{"name":n,"start_date":s,"end_date":e} for n,s,e in __import__("quantlab.portfolio.construction",fromlist=["BLOCKS"]).BLOCKS],"artifacts":artifacts,"limitations":list(result.limitations),"no_policy_ranking":True,"no_parameter_tuning":True,"completed":True}
        (temporary/FILES[5]).write_bytes(canonical_json(canonical_identity_value(manifest))+b"\n");temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary,ignore_errors=True);raise
    return result


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--phase10b-root",type=Path,default=DEFAULT_PHASE10B_ROOT);parser.add_argument("--phase65-root",type=Path,default=DEFAULT_PHASE65_ROOT);parser.add_argument("--output-root",type=Path,default=DEFAULT_OUTPUT_ROOT);args=parser.parse_args(argv)
    result=run_risk_policy_outcome(phase10b_root=args.phase10b_root,phase65_root=args.phase65_root,output_root=args.output_root);print(f"Phase 10C complete: {len(result.observations)} observations");return 0


if __name__=="__main__": raise SystemExit(main())
