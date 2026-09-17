from __future__ import annotations
import pandas as pd
import pytest
from strategy.relative_strength import calculate_relative_strength, calculate_sector_relative_strength

def _series(closes, start="2026-01-01", freq="D"):
    return pd.DataFrame({"time": pd.date_range(start, periods=len(closes), freq=freq), "close": closes})

def test_relative_strength_calculates_stock_minus_benchmark_return(monkeypatch):
    import strategy.relative_strength as module
    data={"AAA":_series([100.,105.,110.,120.]),"VNINDEX":_series([100.,102.,104.,106.])}
    monkeypatch.setattr(module,"load_price_data",lambda symbol:data[symbol])
    result=calculate_relative_strength("AAA",period=2)
    assert result["available"] is True
    assert result["stock_return"]==pytest.approx(14.29,abs=.01)
    assert result["index_return"]==pytest.approx(3.92,abs=.01)
    assert result["relative_strength"]==pytest.approx(10.37,abs=.01)

def test_relative_strength_as_of_date_excludes_future_data(monkeypatch):
    import strategy.relative_strength as module
    data={"AAA":_series([100.,110.,500.]),"VNINDEX":_series([100.,105.,50.])}
    monkeypatch.setattr(module,"load_price_data",lambda symbol:data[symbol])
    result=calculate_relative_strength("AAA",period=1,as_of_date="2026-01-02")
    assert result["available"] is True
    assert result["stock_return"]==pytest.approx(10.)
    assert result["index_return"]==pytest.approx(5.)
    assert result["relative_strength"]==pytest.approx(5.)

def test_relative_strength_requires_period_plus_one_common_sessions(monkeypatch):
    import strategy.relative_strength as module
    stock=_series([100.,105.,110.,115.]); benchmark=_series([100.,102.,104.])
    monkeypatch.setattr(module,"load_price_data",lambda symbol:stock if symbol=="AAA" else benchmark)
    result=calculate_relative_strength("AAA",period=3)
    assert result["available"] is False and pd.isna(result["relative_strength"])

def test_relative_strength_handles_missing_intersection_dates(monkeypatch):
    import strategy.relative_strength as module
    stock=_series([100.,110.,120.,130.])
    benchmark=pd.DataFrame({"time":pd.to_datetime(["2026-01-01","2026-01-02","2026-01-04","2026-01-05"]),"close":[100.,105.,110.,115.]})
    monkeypatch.setattr(module,"load_price_data",lambda symbol:stock if symbol=="AAA" else benchmark)
    result=calculate_relative_strength("AAA",period=2)
    assert result["available"] is True
    assert result["stock_return"]==pytest.approx(30.)
    assert result["index_return"]==pytest.approx(10.)
    assert result["relative_strength"]==pytest.approx(20.)

def test_sector_relative_strength_uses_equal_weight_sector_return(monkeypatch):
    import strategy.relative_strength as module
    data={"AAA":_series([100.,110.,120.]),"BBB":_series([100.,105.,110.]),"CCC":_series([100.,120.,130.])}
    monkeypatch.setattr(module,"load_price_data",lambda symbol:data[symbol])
    result=calculate_sector_relative_strength("AAA",{"AAA":"Banks","BBB":"Banks","CCC":"Banks"},period=2)
    assert result["available"] is True
    assert result["sector"]=="Banks" and result["sector_universe"]==3 and result["sector_eligible"]==3
    assert result["stock_return"]==pytest.approx(20.)
    assert result["sector_return"]==pytest.approx(20.)
    assert result["relative_strength"]==pytest.approx(0.)

def test_sector_relative_strength_excludes_insufficient_member(monkeypatch):
    import strategy.relative_strength as module
    data={"AAA":_series([100.,110.,120.]),"BBB":_series([100.,105.,110.]),"CCC":_series([100.,120.])}
    monkeypatch.setattr(module,"load_price_data",lambda symbol:data[symbol])
    result=calculate_sector_relative_strength("AAA",{"AAA":"Banks","BBB":"Banks","CCC":"Banks"},period=2)
    assert result["available"] is True
    assert result["sector_universe"]==3 and result["sector_eligible"]==2
    assert result["sector_return"]==pytest.approx(15.)
    assert result["relative_strength"]==pytest.approx(5.)

def test_sector_relative_strength_respects_as_of_date(monkeypatch):
    import strategy.relative_strength as module
    data={"AAA":_series([100.,110.,500.]),"BBB":_series([100.,105.,300.])}
    monkeypatch.setattr(module,"load_price_data",lambda symbol:data[symbol])
    result=calculate_sector_relative_strength("AAA",{"AAA":"Banks","BBB":"Banks"},period=1,as_of_date="2026-01-02")
    assert result["available"] is True
    assert result["stock_return"]==pytest.approx(10.)
    assert result["sector_return"]==pytest.approx(7.5)
    assert result["relative_strength"]==pytest.approx(2.5)

def test_sector_relative_strength_requires_sector_mapping(monkeypatch):
    import strategy.relative_strength as module
    monkeypatch.setattr(module,"load_price_data",lambda symbol:_series([100.,110.,120.]))
    result=calculate_sector_relative_strength("AAA",{},period=2)
    assert result["available"] is False
    assert result["sector"] is None
    assert pd.isna(result["sector_return"]) and pd.isna(result["relative_strength"])
