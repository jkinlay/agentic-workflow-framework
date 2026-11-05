"""Teams reporting port. Requires a separately qualified injected transport."""
from agentic.reporting.platform import PlatformReportingAdapter


class ReportingAdapter(PlatformReportingAdapter):
    platform = 'teams'
    # Conservative application byte limit; selected card/host needs qualification.
    max_payload_bytes = 20000
