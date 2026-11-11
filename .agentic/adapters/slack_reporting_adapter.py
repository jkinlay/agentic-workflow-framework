"""Slack reporting adapter, OFF by default; transport qualification is separate."""
from agentic.reporting.platform import SlackReportingAdapter as ReportingAdapter
from agentic.reporting.http_transport import SlackTransport as Transport
