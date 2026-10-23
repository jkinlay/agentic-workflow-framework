### Distribution and source preparation

The supported distribution entry point is `install_awf.py`. The builder generates that launcher, the packaged skill, and release metadata. Source: `7fcaeece:scripts/build_skill_distribution.py:149`.

After independently verifying and extracting the complete distribution, prepare a separate source cache through the packaged helper:

```text
<python> -B <install-dir>/distribution/awf/scripts/prepare_release.py --cache-dir <install-dir>/release-cache --version 1.9.5
```

Command interface: `7fcaeece:global/awf-portable/scripts/prepare_release.py:241`. The example locations are operator-selected.

Expected success:

- Exit 0.
- `status: BUNDLED_RELEASE_PREPARED`.
- `cache_verified: true`.
- `source_verified: true`.
- Returned `version` and `requested_version` identify 1.9.5.
- Returned source and catalog locations match the selected cache.

Sources: `7fcaeece:global/awf-portable/scripts/prepare_release.py:189`; `7fcaeece:global/awf-portable/scripts/prepare_release.py:219`; `7fcaeece:global/awf-portable/tests/test_prepare_release.py:87`.

A 1.9.4 bundle requested as 1.9.5 must reject; it must not silently substitute another release. An incomplete, different, or corrupted existing cache is preserved. Select a new dedicated cache after investigating the rejection. Sources: `7fcaeece:global/awf-portable/scripts/prepare_release.py:121`; `7fcaeece:global/awf-portable/tests/test_prepare_release.py:136`; `7fcaeece:global/awf-portable/tests/test_prepare_release.py:230`.

## 3. INSTALL TO ACTIVE

**All three OS sequences below are conditional on the publication checkpoint in §2. Their successful execution with the integrated 1.9.5 release is UNVERIFIED.**

