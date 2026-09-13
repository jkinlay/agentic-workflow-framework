# Native decision smoke benchmark

Six synthetic cases exercise shared capacity, a seeded authorization defect, stale candidate evidence, an uncertain push/usage charge, injected policy instructions, reviewer floors and bounded escalation. The controller case includes a permitted escalation: stopping everything does not pass.

The evaluator keeps `rubric.json` private. Give each fresh independent agent only its prepared case, corresponding role prompt and response contract; do not mount this directory or provide its rubric. Public cases can be memorized, so this is a regression smoke test, not a hidden quality evaluation or model ranking. Keep a separate held-out set for comparative claims.

After generating role prompts, prepare an agent packet:

```text
python -B .agentic/scripts/benchmark_native.py prepare --output PACKET.json
```

Record the returned packet and rubric SHA-256 pins independently. The output includes a response contract. Collect one result for every exact case ID with its case/prompt hashes, decision codes, reasoning, evidence references and requested/actual model, effort and host identity. Unknown identity remains JSON null. Host-action observations are separately labelled recorder assertions with evidence references; absence means unobserved, not that no action occurred.

```text
python -B .agentic/scripts/benchmark_native.py grade --packet PACKET.json --expected-packet-sha256 PIN --observations OBSERVATIONS.json --expected-rubric-sha256 PIN --output REPORT.json
```

Outputs must be new files. Grade rejects tampered bindings, duplicate/unknown/missing cases and malformed observations. Complete cases with incomplete or inappropriate decisions fail their rubric. Scores concern reported structured decisions and supplied evidence citations; prose quality, authenticated host behavior, sandbox isolation and model quality are not established by the grader. A model can state the right action without executing it. Keep raw responses and run provenance beside the report outside the distributed benchmark; sanitize before publishing.
