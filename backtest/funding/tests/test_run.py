import json

from backtest.funding import run
from backtest.funding.tests.test_engine import candles, funding


def test_cli_json_without_network(monkeypatch, capsys):
    s = candles(3)
    monkeypatch.setattr(run, "download_klines", lambda *a, **kw: s.copy())
    monkeypatch.setattr(run, "download_funding", lambda *a, **kw: funding(s))
    run.main(["--symbol", "BTCUSDT", "--start", "2023-01-01", "--end", "2023-01-02",
              "--funding-window", "1", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["entries"] == 1
    assert result["cash_pnl"] == 0
    assert "buy_hold_pnl" in result


def test_cli_margin_options_and_summary(monkeypatch, capsys):
    s = candles(3)
    monkeypatch.setattr(run, "download_klines", lambda *a, **kw: s.copy())
    monkeypatch.setattr(run, "download_funding", lambda *a, **kw: funding(s))
    original = run.run_backtest

    def check_config(spot, perp, f, config):
        assert config.rebalance_margin_ratio == .4
        assert config.liquidation_fee == .02
        return original(spot, perp, f, config)

    monkeypatch.setattr(run, "run_backtest", check_config)
    run.main(["--start", "2023-01-01", "--end", "2023-01-02",
              "--funding-window", "1", "--rebalance-margin-ratio", ".4",
              "--liquidation-fee", ".02"])
    output = capsys.readouterr().out
    assert "Rebalanceos: 0" in output
    assert "Liquidaciones: 0" in output
    assert "Coste de rebalanceo:" in output
