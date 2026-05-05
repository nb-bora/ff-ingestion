from __future__ import annotations

from unittest.mock import MagicMock

import infrastructure.aws.xray_config as xr


def test_current_trace_header_returns_none_when_xray_disabled(monkeypatch):
    monkeypatch.setattr("infrastructure.aws.xray_config.settings.enable_xray", False)
    assert xr.current_trace_header() is None


def test_current_trace_header_includes_root_parent_sampled(monkeypatch):
    monkeypatch.setattr("infrastructure.aws.xray_config.settings.enable_xray", True)

    fake_recorder = MagicMock()
    fake_segment = MagicMock(trace_id="1-abc", id="seg-id-1")
    fake_recorder.current_segment.return_value = fake_segment

    monkeypatch.setattr("infrastructure.aws.xray_config._xray_recorder", fake_recorder)

    header = xr.current_trace_header()
    assert "Root=1-abc" in header
    assert "Parent=seg-id-1" in header
    assert "Sampled=1" in header
