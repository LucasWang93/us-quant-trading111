"""Charts and run reports. Plotting imports matplotlib lazily via ``usquant.reporting.plots``."""

from usquant.reporting.report import format_metrics_table, write_run

__all__ = ["format_metrics_table", "write_run"]
