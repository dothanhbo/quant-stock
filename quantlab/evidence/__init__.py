"""Immutable prospective paper-portfolio evidence.

The package deliberately keeps portfolio evidence separate from the Forward
selection/outcome ledger.  Importing it has no database, cache, provider, or
operational side effects.
"""

from .prospective_portfolio import (
    DEFAULT_EVIDENCE_DATABASE_PATH,
    EVIDENCE_CONTRACT,
    EVIDENCE_VERSION,
    PaperEventCursor,
    ProspectiveEvidenceCaptureResult,
    ProspectiveEvidenceStatus,
    ProspectiveFillEvidence,
    ProspectivePortfolioEvidenceLedger,
    ProspectivePortfolioEvidenceRecord,
    ProspectivePositionEvidence,
    ProspectiveExitEvidence,
    capture_prospective_portfolio_evidence,
    inspect_prospective_portfolio_evidence,
    paper_store_identity,
    read_paper_account_epoch,
    read_paper_event_cursor,
    resolve_evidence_database_path,
)

__all__ = (
    "DEFAULT_EVIDENCE_DATABASE_PATH",
    "EVIDENCE_CONTRACT",
    "EVIDENCE_VERSION",
    "PaperEventCursor",
    "ProspectiveEvidenceCaptureResult",
    "ProspectiveEvidenceStatus",
    "ProspectiveFillEvidence",
    "ProspectivePortfolioEvidenceLedger",
    "ProspectivePortfolioEvidenceRecord",
    "ProspectivePositionEvidence",
    "ProspectiveExitEvidence",
    "capture_prospective_portfolio_evidence",
    "inspect_prospective_portfolio_evidence",
    "paper_store_identity",
    "read_paper_account_epoch",
    "read_paper_event_cursor",
    "resolve_evidence_database_path",
)
