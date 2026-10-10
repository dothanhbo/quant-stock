# Research Synthesis V1 â€” publication boundary and private replay

EXPLORATORY_ONLY / LEGACY_UNVERIFIED. This scoped commit preserves the existing
synthesis source, note, aggregate results, hashes and preservation metadata.
It contains no OHLCV, SQLite, CSV, GZIP, provider responses or private archives.
Its parent is the unchanged research checkpoint
1f3ad9ee82839d21e15df0849e11198f466b5042 (49 source/metadata files).
The copied synthesis files match their original hashes exactly; .gitattributes
preserves those bytes independently of Git line-ending conversion.

A Git-only checkout is insufficient to run synthesize.py. It needs the exact
saved daily IC series and immutable input bytes referenced in
outputs/source_manifest.json. Do not fetch substitutes or use current market.db.
Restore into a separate clean workspace with the original repository layout:

- MOM20/VOL20: obtain permitted private access to
  mom20_h10_complete_v1_private.zip and vol20_h10_complete_v1_private.zip.
  Verify the parent's research/archive/mom20_vol20_checkpoint_20261010/
  RESTORE_MANIFEST.json, and use restore_private_studies.py with
  --archives-dir PRIVATE_ARCHIVES --destination NEW_EMPTY_PARENT. Place the
  restored sibling study directories under research/alpha_hypotheses/.
- VCI ADX: obtain permitted private access to vci-adx-exploratory-full-checkpoint.zip
  and payload_manifest.json. ZIP SHA256:
  b5ffa87755bab99d1a5d8974a459ef6e098b2d5c23600f00873f58147e567ad3.
  Verify all 98 entries using each all_file_identities entry's sha256/bytes fields.
  Restore its original research/
  alpha_hypotheses/vci_adx14_h10_exploratory_20261009/ and research/archive/
  vci_first_pilot_20261009_exploratory_only/ trees without overwriting files.
  All archive and current-file identities verified; no original artifact changed.
- Verify every reference in outputs/source_manifest.json. Use the documented
  synthesis command with a new nonexistent output directory. It verifies saved
  artifacts and computes summaries only; no factor experiment is rerun.
  Compare the numeric synthesis and source-manifest hashes with the originals.

Frozen study source is restored with the private artifacts. No uncommitted UI
or production implementation is required. VCI ADX is an incompatible sample;
no ranking, scientific qualification or independent-confirmation claim follows.

The complete private checkpoints and their manifests need permitted off-device
backup. No such backup is verified here. Provider redistribution rights remain
unverified; no data/archive is uploaded. No production activation, prospective
enrollment, source acquisition, study rerun or merge occurs in this publication.
