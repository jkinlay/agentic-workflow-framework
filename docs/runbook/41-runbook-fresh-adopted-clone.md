# Fresh clone of an adopted project

Version 1.9.6. An adopted repository contains AWF's installed files and accepted
adoption receipt. Its ignored `.agentic/.venv` runtime belongs to one machine;
Git does not copy it. Provisioning that runtime is the only recovery step.
No PR or commit results, and the adoption receipt and receipt-changing commit
stay unchanged. Existing release trust and adoption acceptance still apply;
provisioning grants no authority and enables no adapter.

Use a host Python matching the verified wheelhouse's Python and platform tags.
For example, `cp312` requires CPython 3.12; `abi3` wheels permit compatible newer
CPython versions. The command rejects incompatible or ambiguous tags before
staging. The host needs only Python's standard library: install nothing into it.
Supply an absolute wheelhouse directory containing only wheels whose complete
SHA-256 hashes and versions match the clone's `.agentic/requirements.lock`.
Obtain these verified wheels through your existing offline distribution process;
do not include checksum text files or other artifacts in the wheelhouse.

From the clone root, replace the absolute project and wheelhouse placeholders:

```powershell
py -3.12 -B -I .agentic/scripts/workflow.py --root 'ABS_PROJECT' provision-runtime --wheelhouse 'ABS_WHEELHOUSE'
```

On POSIX, use the corresponding host interpreter:

```sh
python3.12 -B -I .agentic/scripts/workflow.py --root '/absolute/project' provision-runtime --wheelhouse '/absolute/wheelhouse'
```

Success exits 0 and prints `Interpreter:` and `Entry point:` absolute paths,
as doctor does. Invalid input exits 2 with the refusal text. Provisioning builds
and validates offline, then atomically replaces a missing, invalid or different runtime.
A repeat provision is a no-op when the runtime is current: it exits 0, preserves
the runtime directory identity and creates no staging directory or backup. It writes
only `.agentic/.venv` and its staging/backup siblings. Replacement retains the
previous runtime in a named backup sibling. Provisioning prints a stderr
WARNING naming that exact path; doctor and status repeat this advisory warning
without changing CONFIGURED/ACTIVE decisions. The backup is not ignored by Git
and must not be committed. Once satisfied with the replacement, run the printed
cleanup command yourself; substitute the exact reported path below:

```powershell
Remove-Item -LiteralPath 'ABS_PROJECT/.agentic/.venv.backup-ID' -Recurse -Force
```

```sh
rm -rf -- '/absolute/project/.agentic/.venv.backup-ID'
```

Failures retain available recovery endpoints and report them too.
It does not rewrite configuration, provenance or the adoption receipt, or run
bootstrap, upgrade, Git or a provider command.

Use the printed interpreter with `-B -I`, the printed entry point, and
`--root ABS_PROJECT status --require-active` for normal operation. Windows uses
`.venv/Scripts/python.exe`; POSIX uses `.venv/bin/python`. Optional local checks
are `verify-installation`, `validate-config`, `doctor` and `git status --porcelain`.
`ACTIVE` still requires independent release trust and observed accepted default
branch bytes; unrelated missing provider evidence is not repaired by provisioning.
