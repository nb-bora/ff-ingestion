from __future__ import annotations

# Compat import path (ff-ingestion-style)
from infrastructure.aws.xray_config import (  # noqa: F401
    begin_segment,
    current_trace_header,
    end_segment,
    init_xray,
    subsegment,
    xray_capture,
)
