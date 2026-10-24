### `check-manifest` failure

The read-only source check is:

```text
<python> -B <verified-release-source>/scripts/build_release.py --check-manifest
```

Interface: `7fcaeece:scripts/build_release.py:197`. Workflow use: `7fcaeece:.github/workflows/manifest-check.yml:47`.

**Success:** exit 0 and manifest digest output. **Failure:** inspect stale/missing/edited manifests, source membership, LF/BOM violations, and release hygiene. The check does not rewrite manifests. Sources: `7fcaeece:scripts/build_release.py:138`; `7fcaeece:scripts/build_release.py:175`.

Do not regenerate manifests to bless corrupted checkout bytes. A repair/build workflow requires a separate reviewed change.
