### Missing or incompatible wheelhouse

Writing bootstrap requires `--runtime-wheelhouse`. Missing dependencies, duplicate artifacts, unrelated files, unpinned hashes, unsafe wheel paths, unsupported schemes, and startup hooks reject. Sources: `7fcaeece:.agentic/lib/agentic/adoption_config.py:329`; `7fcaeece:.agentic/lib/agentic/adoption_config.py:369`.

Replace the supplied wheelhouse through the approved artifact process. Bootstrap validates isolated imports and final-path runtime identity before completion. Source: `7fcaeece:.agentic/lib/agentic/adoption_config.py:469`.

