"""走行日誌の純粋部品の検証。時計も線も要らない（辞書と文字列だけ）。"""

from fakes import CannedMonTransport, FakeTransport
from services.run_journal import (
    format_live_delta,
    format_m_delta,
    format_run_end,
    format_run_start,
    live_delta,
    read_m_counts,
    run_csv_name,
    safe_name,
)


def test_safe_name_strips_unsafe() -> None:
    assert safe_name("高速日時変更 Pico用") == "高速日時変更 Pico用"
    assert safe_name('a/b\\c:d*e?"<>|') == "a_b_c_d_e_____"
    assert safe_name("") == "unnamed"


def test_run_csv_name_shape() -> None:
    name = run_csv_name("20260913-001200", "pico", "高速日時変更 Pico用")
    assert name == "run_20260913-001200_pico_高速日時変更 Pico用.csv"


def test_live_delta_diffs_known_keys() -> None:
    before = {"put": 10, "sent": 100, "dropped": 0, "keepalive": 50}
    after = {"put": 70, "sent": 400, "dropped": 3, "keepalive": 200}
    assert live_delta(before, after) == {
        "put": 60,
        "sent": 300,
        "dropped": 3,
        "keepalive": 150,
    }


def test_live_delta_ignores_unknown_and_missing() -> None:
    assert live_delta({}, {"put": 5}) == {"put": 5}
    assert live_delta({"put": 5}, {}) == {"put": -5}
    assert live_delta({"x": 1}, {"x": 2, "put": 1}) == {"put": 1}


def test_format_live_delta_marks_dropped() -> None:
    text = format_live_delta({"put": 60, "sent": 300, "dropped": 0})
    assert "dropped=+0" in text
    text = format_live_delta({"put": 60, "sent": 300, "dropped": 3})
    assert "dropped=+3" in text
    assert "滞留あり" in text


def test_format_m_delta_with_base() -> None:
    text = format_m_delta(
        {"press": 100, "ok": 500, "ng": 2}, {"press": 130, "ok": 800, "ng": 2}
    )
    assert "press=+30" in text
    assert "ng=+0" in text


def test_format_m_delta_without_base() -> None:
    text = format_m_delta(None, {"press": 130, "ok": 800, "ng": 2})
    assert "基準なし" in text


def test_format_m_delta_when_unreadable() -> None:
    assert "取得失敗" in format_m_delta({"press": 1, "ok": 1, "ng": 0}, None)
    assert "基準なし" in format_m_delta(None, None)


def test_format_run_start_contains_identity() -> None:
    text = format_run_start(
        cmd_name="高速日時変更 Pico用",
        profile="pico",
        transport="pico_uart",
        dwell_ms=16,
        repeat_ms=24,
    )
    assert "高速日時変更 Pico用" in text
    assert "pico_uart" in text
    assert "dwell=16ms" in text
    assert "repeat=24ms" in text


def test_format_run_end_contains_verdict() -> None:
    text = format_run_end(
        cmd_name="高速日時変更 Pico用",
        reason="手動停止",
        seconds=12.5,
        live_text="put=+60 sent=+300 dropped=+0",
        m_text="press=+30 ok=+200 ng=+0",
        csv_path="log/run_xxx.csv",
        rows=300,
    )
    assert "手動停止" in text
    assert "12.5秒" in text
    assert "run_xxx.csv" in text


def test_read_m_counts_parses_monitor() -> None:
    transport = CannedMonTransport(
        ["monitor on", "mon empty=0 reply=0 press=5 ok=10 ng=1"]
    )
    assert read_m_counts(transport, tries=2, poll_s=0.0, settle_s=0.0) == {
        "press": 5,
        "ok": 10,
        "ng": 1,
    }


def test_read_m_counts_without_ser_is_empty() -> None:
    assert read_m_counts(FakeTransport(), tries=1, poll_s=0.0, settle_s=0.0) == {}


def test_read_m_counts_takes_last_line() -> None:
    transport = CannedMonTransport(
        [
            "monitor on",
            "mon empty=0 reply=0 press=5 ok=10 ng=1",
            "mon empty=0 reply=0 press=7 ok=14 ng=1",
        ]
    )
    found = read_m_counts(transport, tries=2, poll_s=0.0, settle_s=0.0)
    assert found["press"] == 7
    assert found["ok"] == 14
