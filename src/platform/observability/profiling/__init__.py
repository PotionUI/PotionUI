"""Per-generation resource profiler -- see ``profiler.py`` for the design writeup."""

from __future__ import annotations

from .profiler import (
    LOG_FILENAME,
    GenerationProfiler,
    add_pinned_bytes,
    configure_settings_manager,
    get_profiler,
    pinned_cum_gb,
    profiling_enabled,
    read_process_rss_gb,
    reset_enabled_cache,
)
from .report import (
    load_rows,
    render_report,
    render_report_from_file,
)

__all__ = [
    "LOG_FILENAME",
    "GenerationProfiler",
    "add_pinned_bytes",
    "configure_settings_manager",
    "get_profiler",
    "load_rows",
    "pinned_cum_gb",
    "profiling_enabled",
    "read_process_rss_gb",
    "render_report",
    "render_report_from_file",
    "reset_enabled_cache",
]
