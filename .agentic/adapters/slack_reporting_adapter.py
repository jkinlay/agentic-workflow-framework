"""Slack reporting adapter, OFF by default; transport qualification is separate."""
from agentic.reporting.platform import PlatformReportingAdapter
from agentic.reporting.http_transport import SlackTransport as Transport


class ReportingAdapter(PlatformReportingAdapter):
    platform = 'slack'
    # Conservative application byte limit, not a claim about Slack API limits.
    max_payload_bytes = 12000
