# MOM20 / VOL20 H10 exploratory checkpoint v1

EXPLORATORY_ONLY / LEGACY_UNVERIFIED. This local isolated Git checkpoint preserves
source, fixed configurations, cohort metadata, frozen engine dependencies, research
notes, summary metadata, validation evidence and artifact identities. It changes
no production source, strategy, schema, registry or operational workflow.

Provider OHLCV/SQLite bytes and all CSV/GZIP observation/results payloads are
excluded from Git. Complete studies, including the immutable input snapshot and
all results, are retained in the two verified private ZIP checkpoints referenced
by RESTORE_MANIFEST.json. Its archive and per-file hashes bind actual restorable
files; this is not hash-only preservation. Provider redistribution rights remain
unverified. No public dataset publication or off-device backup is claimed.

MOM20 was verified and archived without a rerun. VOL20 used the identical snapshot,
fixed 100-ticker cohort and available VNINDEX calendar, one prespecified 20-return
sample-standard-deviation definition, score=-VOL20, and exact H10 labels. It reused
the generic panels and existing Rank IC primitives without modifying the built-in
factor whitelist. No parameter search, statistical significance, alpha,
profitability or independent-confirmation claim is made.

The study-specific `.gitattributes` preserves exact source/metadata bytes so that
the recorded frozen-code hashes survive Git line-ending conversion.

## Restore and reproduce

Use the recorded runtime versions. Restore both studies into a NEW directory:

```powershell
python restore_private_studies.py --archives-dir "PRIVATE_ARCHIVE_DIRECTORY" --destination "NEW_EMPTY_PARENT"
```

The script verifies both ZIP hashes and every uncompressed file before extraction,
then verifies the restored bytes. It performs no research and never overwrites work.
After restoration, a future authorized VOL20 replay can use:

```powershell
python -B "NEW_EMPTY_PARENT/vol20_h10_exploratory_20261010/run_study.py" --output "NEW_EMPTY_PARENT/vol20_replay"
```

The VOL20 script resolves the frozen MOM20 snapshot and engine from the sibling
study directory and blocks networking and other SQLite targets. Do not rerun
freeze_inputs.py or replace the snapshot with the current canonical database.
Compare replay output hashes with the original result hashes. Git-only checkout
is insufficient for research replay until the private checkpoints are restored.

The private archives are verified local filesystem preservation. Access to that
storage is required for replay; transfer or publication requires appropriate rights.
