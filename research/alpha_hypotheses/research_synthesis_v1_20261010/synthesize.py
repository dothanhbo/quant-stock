"""Verify preserved artifacts and summarize saved ICs; never execute a factor study."""
import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
LABEL = "EXPLORATORY_ONLY / LEGACY_UNVERIFIED"
STUDIES = {
    "ADX14": "vci_adx14_h10_exploratory_20261009",
    "MOM20": "mom20_h10_exploratory_20261010",
    "VOL20": "vol20_h10_exploratory_20261010",
}
PINNED = {
    "ADX14": ("1830a90abb97d7dbf25c620fc43715fb303164d2e771d013b8c0d508070f6fd4", "4387735cff950cfa90e610c287f189441c00c8636ed9ba4e88a1f098dad262b8"),
    "MOM20": ("e454e155742d4af334de5dda280df136cc8d7445bddd5a56cfac05518b7f56c8", "538264ed7baf7fcff11c6677fff2d5e1852bb44cceeebda3d00d36b63134fd60"),
    "VOL20": ("e5a2a8f15c455d50b735507b7562df23df12a6d0c72d4b0869b0e9c0eefb2f78", "96ce54505584d645d0f190329e42a680781167b48bafe184ce8b0fac8c04cbbb"),
}


def audit(event, args):
    if event.startswith(("sqlite3.", "socket.", "subprocess.", "urllib.")):
        raise RuntimeError(f"Forbidden during saved-output synthesis: {event}")


sys.addaudithook(audit)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-14)


def load_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New, nonexistent directory")
    out = parser.parse_args().output.resolve()
    check(not out.exists(), "Refusing to overwrite any existing output")
    source_hashes, summaries, daily, derived, manifest_counts = {}, {}, {}, {}, {}
    bookkeeping = None

    def bind(path, expected=None):
        path = path.resolve()
        check(path.is_relative_to(ROOT), "Source outside preserved repository artifacts")
        digest = sha(path)
        check(expected is None or digest == expected, f"SHA256 mismatch: {path}")
        source_hashes[path.relative_to(ROOT).as_posix()] = digest
        return digest

    for factor, folder in STUDIES.items():
        study = ROOT / "research/alpha_hypotheses" / folder
        summary_path, daily_path = study / "outputs/summary.json", study / "outputs/daily_ic.csv"
        bind(summary_path, PINNED[factor][0])
        bind(daily_path, PINNED[factor][1])
        summary, config = read_json(summary_path), read_json(study / "experiment.json")
        check(summary["classification"] == config["classification"] == LABEL, "Classification mismatch")
        code = read_json(study / "outputs/code_manifest.json")
        bind(study / "outputs/code_manifest.json")
        bind(study / "experiment.json", code["configuration_sha256"])
        bind(study / "run_study.py", code["runner_sha256"])
        check(hashlib.sha256(canonical(code)).hexdigest() == summary["code_identity"], "Code identity mismatch")
        check(summary["configuration_sha256"] == code["configuration_sha256"], "Configuration identity mismatch")
        input_path = ROOT / config["input_relative_path"] if factor == "ADX14" else study / config["input_relative_path"]
        bind(input_path, config["input_sha256"])
        check(config["input_sha256"] == summary["input_sha256"], "Input identity mismatch")
        engine = study / "code_reference" if factor != "VOL20" else study / config["engine_root_relative_path"]
        for name, digest in code["module_sha256"].items():
            bind(engine / name, digest)
        if factor == "ADX14":
            checksum = study / "outputs/SHA256SUMS"
            bind(checksum)
            entries = {"outputs/" + line.split(None, 1)[1]: line.split(None, 1)[0] for line in checksum.read_text().splitlines()}
            preserved = ROOT / "research/archive/vci_first_pilot_20261009_exploratory_only/manifest.json"
            bind(preserved, config["preservation_manifest_sha256"])
            bind(study / "RESEARCH_NOTE.md")
        else:
            checksum = study / "outputs/SHA256.json"
            bind(checksum)
            entries = read_json(checksum)["files"]
            mom = ROOT / "research/alpha_hypotheses" / STUDIES["MOM20"]
            bind(mom / "inputs/cohort_v1_manifest.json", config["cohort_manifest_sha256"])
            bind(mom / "inputs/cohort_v1_tickers.txt", config["membership_sha256"])
            bind(mom / "inputs/manifest.json")
            check(len(config["cohort"]) == len(set(config["cohort"])) == 100, "Cohort count mismatch")
            check((mom / "inputs/cohort_v1_tickers.txt").read_text().splitlines() == config["cohort"], "Membership mismatch")
            if factor == "VOL20":
                bind(study / config["engine_manifest_relative_path"], code["engine_manifest_sha256"])
        for name, digest in entries.items():
            bind(study / name, digest)
        manifest_counts[factor] = len(entries)
        bind(study / "verification.json")
        verification = read_json(study / "verification.json")
        for name, digest in verification.get("result_hashes", {}).items():
            actual = sha(study / "outputs" / name)
            if actual != digest:
                check(factor == "VOL20" and name == "SHA256.json", "Unexplained saved-verification mismatch")
                previous = read_json(checksum)
                previous["files"].pop("verification.json")
                before = (json.dumps(previous, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
                check(hashlib.sha256(before).hexdigest() == digest, "Checksum-manifest history not explained")
                bookkeeping = {"path": checksum.relative_to(ROOT).as_posix(), "older_verification_sha256": digest,
                               "current_sha256": actual, "explanation": "Old digest exactly reproduces current manifest with verification.json entry removed; result contents unchanged."}

        rows = load_csv(daily_path)
        dates = [row["session_date"] for row in rows]
        check(dates == sorted(set(dates)), "Duplicate or unordered dates")
        for row in rows:
            for key in ["classification", "input_sha256", "configuration_sha256", "code_identity"]:
                check(row[key] == summary[key], "Daily observation binding mismatch")
            check(bool(row["rank_ic"]) != bool(row["undefined_reason"]), "Undefined IC ambiguity")
        valid = [(row["session_date"], float(row["rank_ic"])) for row in rows if row["rank_ic"]]
        check(all(math.isfinite(value) and -1 <= value <= 1 for _, value in valid), "Invalid saved IC")
        check(len(valid) == summary["defined_ic_dates"], "Defined-date count mismatch")
        check(len(rows) - len(valid) == summary["undefined_ic_dates"], "Undefined-date count mismatch")
        check(close(math.fsum(value for _, value in valid) / len(valid), summary["overall_equal_date_mean_rank_ic"]), "Saved mean mismatch")
        check(sum(int(row["eligible_pairs"]) for row in rows) == summary["pairwise_eligible_observations"], "Eligible-count mismatch")
        check(valid[0][0] == summary["first_defined_ic_date"] and valid[-1][0] == summary["last_defined_ic_date"], "Date bounds mismatch")
        annual = []
        for year in sorted({date[:4] for date, _ in valid}):
            values = [value for date, value in valid if date.startswith(year)]
            annual.append({"year": year, "defined_dates": len(values), "mean_ic": math.fsum(values) / len(values)})
        if factor != "ADX14":
            saved = load_csv(study / "outputs/chronological_slices.csv")
            check(len(saved) == len(annual), "Annual coverage mismatch")
            for old, new, summary_slice in zip(saved, annual, summary["chronological_slices"]):
                check(old["year"] == new["year"] == summary_slice["year"], "Year mismatch")
                check(int(old["defined_ic_dates"]) == new["defined_dates"] == summary_slice["defined_ic_dates"], "Annual count mismatch")
                check(close(float(old["equal_date_mean_ic"]), new["mean_ic"]) and close(summary_slice["equal_date_mean_ic"], new["mean_ic"]), "Annual mean mismatch")
            check(sum(value > 0 for _, value in valid) == summary["positive_ic_dates"], "Positive-date count mismatch")
        annual_changes = sum(a["mean_ic"] * b["mean_ic"] < 0 for a, b in zip(annual, annual[1:]))
        derived[factor] = {"annual": annual, "annual_sign_changes": annual_changes}
        summaries[factor], daily[factor] = summary, rows

    mom, vol = summaries["MOM20"], summaries["VOL20"]
    for key in ["input_sha256", "snapshot_id", "logical_fingerprint", "cohort_identity", "window"]:
        check(mom[key] == vol[key], "MOM/VOL dataset compatibility mismatch")
    check(mom["spec_fingerprints"]["outcome"] == vol["spec_fingerprints"]["outcome"], "H10 contract mismatch")
    mrows = {row["session_date"]: row for row in daily["MOM20"]}
    vrows = {row["session_date"]: row for row in daily["VOL20"]}
    check(set(mrows) == set(vrows), "Unmatched scheduled dates")
    check(all(mrows[d]["target_session"] == vrows[d]["target_session"] for d in mrows), "H10 target mismatch")
    matched = [d for d in sorted(mrows) if mrows[d]["rank_ic"] and vrows[d]["rank_ic"]]
    check(len(matched) == mom["defined_ic_dates"] == vol["defined_ic_dates"], "Unmatched defined ICs")
    x = [float(mrows[d]["rank_ic"]) for d in matched]
    y = [float(vrows[d]["rank_ic"]) for d in matched]
    mx, my = math.fsum(x) / len(x), math.fsum(y) / len(y)
    correlation = math.fsum((a-mx)*(b-my) for a, b in zip(x, y)) / math.sqrt(math.fsum((a-mx)**2 for a in x) * math.fsum((b-my)**2 for b in y))
    sources = {"classification": LABEL, "sha256": dict(sorted(source_hashes.items()))}
    original = HERE / "outputs/source_manifest.json"
    if original.exists():
        check(sources == read_json(original), "Preserved synthesis source identities changed")
    result = {"classification": LABEL, "studies": summaries, "saved_daily_aggregations": derived,
              "mom_vol_daily_ic_correlation": {"method": "Pearson correlation of saved daily Spearman Rank ICs; date join; no fill",
                  "pearson_r": correlation, "matched_defined_dates": len(matched), "unmatched_defined_dates": 0,
                  "unmatched_scheduled_dates": 0, "joint_undefined_dates": len(mrows)-len(matched),
                  "first_date": matched[0], "last_date": matched[-1]},
              "verification": {"manifest_entry_counts": manifest_counts, "distinct_source_files_verified": len(source_hashes),
                  "checksum_manifest_history": bookkeeping, "ohlcv_queries": 0, "factor_runs": 0, "network_calls": 0},
              "decisions": {"ADX14": "INSUFFICIENT_EVIDENCE", "MOM20": "RETAIN_FOR_PROSPECTIVE_CONSIDERATION", "VOL20": "DO_NOT_PRIORITIZE"}}
    out.mkdir(parents=True)
    for name, obj in [("synthesis.json", result), ("source_manifest.json", sources)]:
        (out / name).write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    hashes = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    checksum = {"classification": LABEL, "files": hashes,
                "runner": {"repository_relative_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
                           "sha256": sha(Path(__file__).resolve())}}
    (out / "SHA256.json").write_text(json.dumps(checksum, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": "COMPLETED", "pearson_r": correlation, "matched_dates": len(matched), "verified_source_files": len(source_hashes), "output": str(out)}))


if __name__ == "__main__":
    main()
