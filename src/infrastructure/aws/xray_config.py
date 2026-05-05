"""AWS X-Ray configuration for FairFare Ingestion Service.

Copie conforme du comportement `ff-ingestion`, adaptée aux noms de settings
du projet `Ingestion`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

from config import settings
from logger import logger

SERVICE_NAME = "ff-ingestion"

_patch_all = None
_xray_recorder = None


def _ensure_xray() -> None:
    global _patch_all, _xray_recorder
    if _xray_recorder is not None:
        return
    from aws_xray_sdk.core import patch_all as pa
    from aws_xray_sdk.core import xray_recorder as xr

    _patch_all = pa
    _xray_recorder = xr


def _noop_capture(_name: str):
    def decorator(fn):
        return fn

    return decorator


@contextmanager
def subsegment(name: str) -> Generator[Any, None, None]:
    if getattr(settings, "enable_xray", False):
        _ensure_xray()
        with _xray_recorder.in_subsegment(name) as sub:
            yield sub
    else:
        yield None


def init_xray(extra_config: dict[str, Any] | None = None) -> None:
    if not getattr(settings, "enable_xray", False):
        logger.debug("AWS X-Ray désactivé pour Ingestion")
        return

    _ensure_xray()
    from aws_xray_sdk.core.async_context import AsyncContext

    config: dict[str, Any] = {"service": SERVICE_NAME, "context": AsyncContext()}
    config["context_missing"] = "IGNORE_ERROR"
    daemon_address = getattr(settings, "aws_xray_daemon_address", None)
    if daemon_address:
        config["daemon_address"] = daemon_address
    if extra_config:
        config.update(extra_config)

    _xray_recorder.configure(**config)
    _patch_all()
    logging.getLogger("aws_xray_sdk").setLevel(logging.ERROR)
    logger.info("AWS X-Ray initialisé pour Ingestion")


def xray_capture(segment_name: str):
    if getattr(settings, "enable_xray", False):
        _ensure_xray()
        return _xray_recorder.capture(segment_name)
    return _noop_capture(segment_name)


def begin_segment(name: str) -> None:
    if getattr(settings, "enable_xray", False):
        _ensure_xray()
        _xray_recorder.begin_segment(name)


def end_segment() -> None:
    if getattr(settings, "enable_xray", False) and _xray_recorder is not None:
        _xray_recorder.end_segment()


def current_trace_header() -> str | None:
    """
    Construit un trace header AWS X-Ray complet pour propagation downstream.

    Format
    ------
    `Root={trace_id};Parent={segment_id};Sampled=1`

    Le `Parent` est nécessaire pour qu'AWS reconstruise correctement la chaîne
    parent → enfant entre services.
    """
    if not getattr(settings, "enable_xray", False) or _xray_recorder is None:
        return None

    seg = _xray_recorder.current_segment()
    if seg is None:
        return None

    parts = [f"Root={seg.trace_id}"]
    seg_id = getattr(seg, "id", None)
    if seg_id:
        parts.append(f"Parent={seg_id}")
    parts.append("Sampled=1")
    return ";".join(parts)
