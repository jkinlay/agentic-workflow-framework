"""One credential grammar for report retention and outward observations."""
from ..child_process import contains_credentials

WITHHELD = 'CREDENTIAL_WITHHELD'


def safe_report(value, *, trusted_keys=False):
    """Withhold entire detected strings/keys; retain no value, excerpt or hash.

    Walk decoded JSON members as well as complete strings, using the host's
    existing retention detector. This does not claim universal secret detection.
    """
    if isinstance(value, str):
        return WITHHELD if contains_credentials(value) else value
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            safe_key = WITHHELD + '_' + str(index) if not trusted_keys and contains_credentials(str(key)) else key
            result[safe_key] = safe_report(item, trusted_keys=trusted_keys)
        return result
    if isinstance(value, list):
        return [safe_report(item, trusted_keys=trusted_keys) for item in value]
    return value
