import json
from pathlib import Path

from level_trader.journal import Journal


def test_record_state_writes_json_atomically(tmp_path: Path) -> None:
    j = Journal(
        trade_log=str(tmp_path / "trades.jsonl"),
        equity_log=str(tmp_path / "equity.jsonl"),
        state_file=str(tmp_path / "state.json"),
    )
    state = {"equity": 10_000.0, "open_positions": [], "num_open": 0}
    j.record_state(state)
    assert j.state_path.exists()
    loaded = json.loads(j.state_path.read_text())
    assert loaded["equity"] == 10_000.0
    assert loaded["num_open"] == 0
    # Overwriting should replace cleanly and leave no .tmp behind.
    j.record_state({"equity": 10_100.0, "open_positions": [], "num_open": 1})
    loaded2 = json.loads(j.state_path.read_text())
    assert loaded2["equity"] == 10_100.0
    assert loaded2["num_open"] == 1
    assert not j.state_path.with_suffix(j.state_path.suffix + ".tmp").exists()


def test_record_state_defaults_path_next_to_equity_log(tmp_path: Path) -> None:
    j = Journal(trade_log=str(tmp_path / "trades.jsonl"), equity_log=str(tmp_path / "equity.jsonl"))
    assert j.state_path == tmp_path / "state.json"
    j.record_state({"equity": 1.0})
    assert j.state_path.exists()
