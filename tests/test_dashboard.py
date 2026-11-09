"""Dashboard contract tests, including a real local server and changing source."""
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import pytest

from datalab.dashboard import SourceStore, build_dashboard, create_dashboard_server


def _bootstrap(path):
    source = path.read_text(encoding="utf-8")
    payload = re.search(r'<script id="bootstrap"[^>]*>(.*?)</script>', source, re.S)
    assert payload
    return json.loads(payload.group(1)), source


@pytest.mark.parametrize("extension", ["csv", "xlsx", "json", "parquet"])
def test_static_export_supported_formats_and_source_hash(tmp_path, extension):
    frame = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "region": ["华东", "华南"], "sales": [10, 20]})
    source = tmp_path / 'inputs' / f"source.{extension}"
    source.parent.mkdir(exist_ok=True)
    if extension == "csv":
        frame.to_csv(source, index=False)
    elif extension == "xlsx":
        frame.to_excel(source, index=False)
    elif extension == "json":
        frame.to_json(source, orient="records")
    else:
        pytest.importorskip("pyarrow")
        frame.to_parquet(source, index=False)
    output = tmp_path / "result.html"
    result = build_dashboard(source, output, date_column="date", metric="sales", group="region")
    bootstrap, html = _bootstrap(output)
    assert bootstrap["live"] is False
    assert result["rows_total"] == 2
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert bootstrap["data"]["records"][1]["sales"] == 20
    assert bootstrap["data"]["defaults"] == {"date": "date", "metric": "sales", "group": "region", "agg": "sum"}
    assert "<script src=" not in html
    assert "https://" not in html
    assert str(tmp_path) not in html


def test_static_export_escapes_script_and_title_injection(tmp_path):
    attack = '</script><script>globalThis.pwned=true</script><img src=x onerror=alert(1)>'
    source = tmp_path / 'inputs' / "xss.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps([{"label": attack, "value": 1, "literal": "__PAGE_TITLE__ __SCRIPT_NONCE__"}]), encoding="utf-8")
    output = tmp_path / "safe.html"
    build_dashboard(source, output, metric="value", title=attack)
    bootstrap, html = _bootstrap(output)
    assert bootstrap["data"]["records"][0]["label"] == attack
    assert bootstrap["data"]["records"][0]["literal"] == "__PAGE_TITLE__ __SCRIPT_NONCE__"
    assert attack not in html
    assert html.count("<script") == 2
    assert "\\u003c/script\\u003e" in html
    assert "&lt;img" in html
    assert "innerHTML" not in html


def test_rate_defaults_and_explicit_aggregation_and_row_limit(tmp_path):
    source = tmp_path / 'inputs' / "rates.csv"
    source.parent.mkdir(exist_ok=True)
    pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "conversion_rate": [.1, .2]}).to_csv(source, index=False)
    output = tmp_path / "rate.html"
    with pytest.raises(ValueError, match="不会截取"):
        build_dashboard(source, output, metric="conversion_rate", max_rows=1)
    assert not output.exists()
    result = build_dashboard(source, output, metric="conversion_rate", max_rows=2)
    bootstrap, html = _bootstrap(output)
    assert bootstrap["data"]["defaults"]["agg"] == "mean"
    assert bootstrap["data"]["columns"][1]["rate"] is True
    assert result["rows_loaded"] == 2 and result["rows_total"] == 2 and result["truncated"] is False
    assert "简单平均" in html and "分子、分母" in html
    original = output.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        build_dashboard(source, output, metric="conversion_rate", agg="count")
    assert output.read_bytes() == original
    count_output = tmp_path / "count.html"
    build_dashboard(source, count_output, metric="conversion_rate", agg="count")
    assert _bootstrap(count_output)[0]["data"]["defaults"]["agg"] == "count_nonmissing"


def test_source_is_not_overwritten_and_invalid_configuration_fails(tmp_path):
    source = tmp_path / 'inputs' / "source.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("sales\n10\n", encoding="utf-8")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖|原始数据目录"):
        build_dashboard(source, source)
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="Unknown metric"):
        build_dashboard(source, tmp_path / "bad.html", metric="missing")
    with pytest.raises(ValueError, match="max_rows"):
        build_dashboard(source, tmp_path / "bad.html", max_rows=0)


def test_dashboard_refuses_hardlink_to_source(tmp_path):
    source = tmp_path / 'inputs' / "source.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("sales\n10\n", encoding="utf-8")
    output = tmp_path / "dashboard.html"
    try:
        os.link(source, output)
    except OSError as exc:
        pytest.skip(f"Hardlinks are unavailable on this test filesystem: {exc}")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        build_dashboard(source, output, metric="sales")
    assert source.read_bytes() == original


@contextmanager
def running_server(source, **options):
    server = create_dashboard_server(source, port=0, interval=2, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def fetch(base, suffix="/api/data"):
    with urlopen(base + suffix, timeout=3) as response:
        return json.load(response)


def wait_for(base, predicate, timeout=9):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = fetch(base)
        if predicate(payload):
            return payload
        time.sleep(.1)
    pytest.fail("Local dashboard did not reach expected source state")


def test_live_server_polls_modified_source_retains_last_good_and_recovers(tmp_path):
    source = tmp_path / 'inputs' / "sales.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("date,sales\n2026-01-01,10\n", encoding="utf-8")
    with running_server(source, date_column="date", metric="sales") as (server, base):
        initial = fetch(base)
        assert initial["records"][0]["sales"] == 10
        original_sha = initial["source"]["sha256"]
        source.write_text("date,sales\n2026-01-01,25\n2026-01-02,30\n", encoding="utf-8")
        changed = wait_for(base, lambda p: p["source"]["sha256"] != original_sha)
        assert changed["rows_total"] == 2 and changed["records"][0]["sales"] == 25
        success = changed["status"]["last_success"]
        source.write_text('date,sales\n"unterminated', encoding="utf-8")
        stale = wait_for(base, lambda p: p["status"]["stale"])
        assert stale["records"] == changed["records"]
        assert stale["status"]["last_success"] == success
        assert stale["status"]["error"]
        source.write_text("date,sales\n2026-01-03,99\n", encoding="utf-8")
        recovered = wait_for(base, lambda p: not p["status"]["stale"] and p["records"][0]["sales"] == 99)
        assert recovered["rows_total"] == 1
        assert recovered["status"]["last_success"] != success
        assert recovered["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        with urlopen(base + "/", timeout=3) as response:
            page = response.read().decode()
            assert '"live":true' in page
            assert response.headers["Cache-Control"] == "no-store"
        assert server.store.interval == 2


def test_server_limits_routes_origin_and_bind_address(tmp_path):
    source = tmp_path / 'inputs' / "source.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("sales\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="loopback"):
        create_dashboard_server(source, host="0.0.0.0", port=0)
    with running_server(source) as (_, base):
        for route in ("/source.csv", "/../source.csv", "/etc/passwd"):
            with pytest.raises(HTTPError) as caught:
                urlopen(base + route, timeout=3)
            assert caught.value.code == 404
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}):
            with pytest.raises(HTTPError) as caught:
                urlopen(Request(base + "/api/data", headers=headers), timeout=3)
            assert caught.value.code == 403
        # Query strings cannot select another file or run a query.
        assert fetch(base, "/api/data?path=/etc/passwd")["records"] == [{"sales": 1}]


def test_unchanged_source_does_not_claim_new_success_and_minimum_interval(tmp_path):
    source = tmp_path / 'inputs' / "source.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("sales\n1\n", encoding="utf-8")
    store = SourceStore(source, interval=.1)
    initial = store.snapshot()
    assert store.interval == 2
    assert store.refresh() is False
    unchanged = store.snapshot()
    assert unchanged["status"]["last_success"] == initial["status"]["last_success"]
    assert unchanged["status"]["last_changed"] == initial["status"]["last_changed"]
    assert unchanged["status"]["last_checked"]
    assert unchanged["status"]["stale"] is False
