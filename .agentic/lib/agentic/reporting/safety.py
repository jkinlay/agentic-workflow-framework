"""One credential grammar for report retention and outward observations."""
from ..child_process import contains_credentials
from .. import ValidationError

WITHHELD = 'CREDENTIAL_WITHHELD'
_SCHEMA_KEYS = {'Token usage', 'Token forecast'}


def safe_report(value, *, trusted_keys=False, credentials=(), secret_refs=(), resolve_secret=None, senders=()):
    """Withhold entire detected strings/keys; retain no value, excerpt or hash.

    Walk decoded JSON members as well as complete strings, using the host's
    existing retention detector. This does not claim universal secret detection.
    Resolve only already admitted sender references. Values live only in this
    call, never on an adapter, in a registry, hash or withholding record.
    """
    try:
        credentials = tuple(credentials) + tuple(sender.resolve_secret(sender.binding['secret_ref'])
            for sender in senders if callable(getattr(sender, 'resolve_secret', None)))
    except Exception:
        raise ValidationError('REPORT_SECRET_UNAVAILABLE') from None
    if secret_refs:
        if resolve_secret is None:
            from .platform import environment_secret
            resolve_secret = environment_secret
        try:
            credentials = tuple(credentials) + tuple(resolve_secret(ref) for ref in secret_refs)
        except Exception:
            raise ValidationError('REPORT_SECRET_UNAVAILABLE') from None

    def walk(item):
        if isinstance(item, str):
            return WITHHELD if contains_credentials(item, credentials=credentials) else item
        if isinstance(item, dict):
            result = {}
            for index, (key, member) in enumerate(item.items()):
                # Fixed schema keys need pattern exemptions (e.g. token metrics),
                # but an exact sender-value match is never exempt.
                exact = any(isinstance(c, str) and c and c in str(key) for c in credentials)
                unsafe = exact or (contains_credentials(str(key)) and
                                   (not trusted_keys or key not in _SCHEMA_KEYS))
                safe_key = WITHHELD + '_' + str(index) if unsafe else key
                result[safe_key] = walk(member)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(member) for member in item]
        return item

    return walk(value)
