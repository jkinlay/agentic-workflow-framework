"""Slack reporting port. Requires a separately qualified injected transport."""
from agentic.reporting.platform import PlatformReportingAdapter


class ReportingAdapter(PlatformReportingAdapter):
    platform = 'slack'
    # Conservative application byte limit, not a claim about Slack API limits.
    max_payload_bytes = 12000
