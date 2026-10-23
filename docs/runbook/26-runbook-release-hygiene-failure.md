### Release-hygiene failure

The source check is:

```text
<python> -B <verified-release-source>/scripts/release_hygiene.py
```

CLI: `7fcaeece:scripts/release_hygiene.py:209`.

**Success:** JSON `status: PASS`. **Failure:** the raised diagnostic lists documentation budgets, operational-version mismatches, damaged text, prompt disagreement, or other named problems. Source: `7fcaeece:scripts/release_hygiene.py:141`.

The source hygiene policy caps runbooks at 1,200 words. This exhaustive final-message runbook exceeds that repository packaging budget and is **not a checked-in, release-hygiene-qualified artifact**. Source: `7fcaeece:scripts/release_hygiene.py:94`.

