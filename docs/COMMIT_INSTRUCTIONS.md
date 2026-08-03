# Commit and Push

## Verify

```powershell
py -m py_compile backtesting\report.py
py -m py_compile backtesting\engine.py
py -m pytest
```

## Smoke Test

```powershell
py -m backtesting.engine --symbol ACB --start 2015-07-16 --end 2026-07-31 --quiet
```

## Commit

```powershell
git add README.md CHANGELOG.md ROADMAP.md docs\releases\v5.2.md backtesting\report.py
git commit -m "release(v5.2): add analytics benchmark and modular reporting"
```

## Push

```powershell
git pull --rebase origin main
git push origin main
git status
```
