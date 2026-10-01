@echo off
set SSL_CERT_FILE=
set SSL_CERT_DIR=
cd /d C:\Users\jkinlay\Documents\GitHub\awf-1.9.2-critic
echo START %DATE% %TIME% > C:\Users\jkinlay\Documents\GitHub\awf-rehearsal-1.9.1\state\awf192-critic.log
codex exec -m gpt-6-astra -c model_reasoning_effort=high -c approval_policy=never -s read-only - < C:\Users\jkinlay\Documents\GitHub\awf-rehearsal-1.9.1\state\awf192-critic-prompt.md >> C:\Users\jkinlay\Documents\GitHub\awf-rehearsal-1.9.1\state\awf192-critic.log 2>&1
echo END %DATE% %TIME% exit=%ERRORLEVEL% >> C:\Users\jkinlay\Documents\GitHub\awf-rehearsal-1.9.1\state\awf192-critic.log
