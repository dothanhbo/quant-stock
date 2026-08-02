# Changelog

## v0.9.0 — Walk-Forward Optimization

### Added

- Trailing ATR Exit Model
- Exit-model factory support
- ATR multiplier CLI arguments
- Trailing ATR parameter grid
- Cross-symbol exit validation
- Exit-model benchmark framework
- Symbol winner matrix
- Walk-forward window generator
- Train parameter selection
- Selected-parameter out-of-sample testing
- Walk-forward summary CSV
- Database coverage checker
- Eight-year historical backfill support

### Fixed

- Fixed intraday look-ahead bug in trailing-stop simulation
- Applied updated trailing levels from the next session
- Fixed missing ATRExitModel import
- Fixed CLI support for trailing_atr
- Fixed incomplete historical-data coverage
- Fixed syntax and indentation issues in research scripts

### Research findings

- No exit model dominated all symbols.
- In-sample Trailing ATR results did not hold out of sample.
- Current priority is entry-strategy research.
