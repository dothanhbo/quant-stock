# Repository cleanup safety

## Preserve research provenance

Research scripts and results can record the hypothesis, configuration, and
evidence behind a frozen policy. Before deleting research material, preserve
the relevant result and conclusion, even when the code is no longer imported
by production.

## Protect runtime data

Never delete canonical market or paper-trading databases as generic workspace
cleanup. This includes `data/market.db`, `data/paper_trading.db`,
`data/paper_trading_v2.db`, and `data/paper_trading_v3.db` when present.
Configured database overrides must be treated with the same care.

## Inspect before deleting

- Compare ambiguous files with tracked counterparts and check imports,
  references, and Git history.
- Do not assume that an untracked or research-only file is disposable.
- Use explicit literal paths for approved targets.
- Avoid globs and broad recursive deletion commands, especially against the
  repository root, `data/`, `research/`, or `research_results/`.
- If a path is ambiguous, outside the repository, or a symlink/junction, stop
  and review it before deletion.
