# Upgrade AWF 1.9.0 to 1.9.1

This corrective patch changes no workflow semantics, configuration, schemas, governance values or model-routing policy. It fixes the cross-platform adoption-status regression test: an ACTIVE project may still show a non-blocking `Host preflight WARN` next action on Windows, as required by the 1.9.0 preflight contract.

Verify the 1.9.1 source/distribution pins and use the existing reviewed upgrade flow. Preserve project configuration, instructions, audit evidence and ownership. No record reissue or governance PR is required solely for this patch.
