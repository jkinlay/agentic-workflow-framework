# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release and its manifest pin. Run a non-writing upgrade plan first. Stop active dispatch and establish exclusive ownership before applying the transactional upgrade; retain backups and recovery records until verification succeeds.

The upgrade recognizes the published 1.9.5 receipt and changes the expected workflow version. Existing project routing, operating choices, reviewer allocation, pins, budgets and accounting remain. Existing projects do not automatically adopt the new twelve-stream or model defaults: request those operating changes separately within reviewed governance ceilings.

Fresh projects default to twelve logical workers. Controller, critic, specialist and ordinary worker defaults are gpt-6.1-sol/high; qualifying simple work uses gpt-6-luna/high. Host capacity still limits actual concurrent dispatch.

Unknown state remains present and receives a read-only archive copy for inspection. Existing 1.9.5 archives are retained without recursively archiving them. Never reset accounting or edit managed receipts to force acceptance.

Verify installed integrity and accepted configuration through the installed isolated interpreter. Human-reviewed adoption acceptance and independent release trust are required for ACTIVE. ACTIVE alone does not enable Jira or start a controller. Automatic activation reporting remains deferred.
