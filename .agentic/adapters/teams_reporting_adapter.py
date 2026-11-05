"""Teams reporting adapter, OFF by default; transport qualification is separate."""
from agentic.reporting.platform import PlatformReportingAdapter
from agentic.reporting.http_transport import TeamsTransport as Transport


class ReportingAdapter(PlatformReportingAdapter):
    platform = 'teams'
    # Conservative application byte limit; selected card/host needs qualification.
    max_payload_bytes = 20000
