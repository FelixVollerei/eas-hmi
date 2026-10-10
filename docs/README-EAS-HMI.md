# EAS-HMI engineering profile in EAFS

EAFS retains the original EAS-HMI implementation because its engineering profile and
MCP tools use typed project models, revision checks, semantic hashes and transactions.
This profile is one of the original thirty EAFS profiles. EAS Visual is separate.

Use `eas-hmi --help` for its command surface, or discover the profile with
`eafs-mcp` tools `eas_contract`, `eas_status`, `eas_query`, `eas_inspect`,
`eas_context`, `eas_preview`, `eas_apply` and `eas_undo`.
An EAS-HMI plan uses `sfe/0.1`, format `eas-hmi`, and the current revision and semantic hash.
Read the [EAFS introduction](EAFS-INTRODUCTION.md), [MCP guide](EAFS-MCP.md) and
[design contract](design-contract.md) for supported operations and validation boundaries.

The fixed synthetic engineering model is `examples/stage4/model.json`.
`python scripts/demo_sfe.py` includes this profile alongside the ordinary-file examples.
Historical engineering reports and Visual demos remain in the donor project and are
not evidence for this export. Current results are in [VALIDATION.md](../VALIDATION.md).
