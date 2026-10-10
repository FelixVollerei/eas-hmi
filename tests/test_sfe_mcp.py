"""Real stdio MCP session for the shared file and EAS-HMI adapters."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from eas_hmi.model import Project, canonical_hash
from eas_hmi.operations.transaction import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "sfe"
EXPECTED_TOOLS = {
    "file_contract", "file_status", "file_inspect", "file_preview", "file_apply",
    "eas_contract", "eas_status", "eas_query", "eas_inspect", "eas_context",
    "eas_preview", "eas_apply", "eas_undo",
}


def content(result):
    value = result.structuredContent
    assert value == json.loads(result.content[0].text)
    assert result.isError == (not value["ok"])
    return value


def test_mcp_stdio_file_and_eas_workflows(tmp_path):
    copied = {}
    for name in (
        "settings.json", "assets.csv", "handover.md", "schematic.svg",
        "budget.xlsx", "equipment.sqlite", "network.drawio",
    ):
        source = SAMPLES / name
        target = tmp_path / name
        target.write_bytes(source.read_bytes())
        copied[name] = target
    project = Project.model_validate_json((ROOT / "examples/stage4/model.json").read_text(encoding="utf-8"))
    state = Store(tmp_path / "eas-project")
    state.initialize(project)

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sfe_files.mcp_server"],
            env={**os.environ, "EAFS_MCP_ROOTS": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert {tool.name for tool in listing.tools} == EXPECTED_TOOLS
            catalog = content(await session.call_tool("file_contract", {}))
            assert catalog["protocol"] == "sfe/0.1" and len(catalog["formats"]) == 30
            assert catalog["formats"]["minecraft-java-world"]["kind"] == "directory"
            json_contract = content(
                await session.call_tool("file_contract", {"format_name": "json"})
            )
            assert json_contract["instruction_set"]["operations"]["set"]["example"]["op"] == "set"
            for name, selector, field in (
                ("assets.csv", {"key_column": "id", "key": "P-01"}, "row"),
                ("handover.md", {"heading": "Findings"}, "body"),
                ("schematic.svg", {"object_id": "pump-label"}, "target"),
            ):
                result = content(
                    await session.call_tool("file_inspect", {"file": str(copied[name]), **selector})
                )
                assert result[field]

            for name, pointer in (
                ("settings.json", "/enabled"),
                ("budget.xlsx", "/sheets/Budget/B5"),
                ("equipment.sqlite", "/tables/equipment/rows/P-01"),
                ("network.drawio", "/pages/2/cells/pump"),
            ):
                source = copied[name]
                original = source.read_bytes()
                plan = json.loads((SAMPLES / (source.stem + ".plan.json")).read_text(encoding="utf-8"))
                status = content(await session.call_tool("file_status", {"file": str(source)}))
                assert status["sha256"] == plan["base"]["sha256"]
                selected = content(
                    await session.call_tool("file_inspect", {"file": str(source), "pointer": pointer})
                )
                assert selected["sha256"] == status["sha256"]
                preview = content(await session.call_tool("file_preview", {"file": str(source), "plan": plan}))
                assert preview["diff"] and preview["committed"] is False
                output = tmp_path / ("edited-" + name)
                applied = content(
                    await session.call_tool(
                        "file_apply", {"file": str(source), "plan": plan, "output": str(output)}
                    )
                )
                after = content(await session.call_tool("file_status", {"file": str(output)}))
                assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
                assert source.read_bytes() == original
                conflict = content(
                    await session.call_tool(
                        "file_apply",
                        {"file": str(output), "plan": plan, "output": str(tmp_path / ("stale-" + name))},
                    )
                )
                assert conflict["code"] == "BASE_CONFLICT"

            denied = content(await session.call_tool("file_status", {"file": str(SAMPLES / "settings.json")}))
            assert denied["code"] == "PATH_NOT_ALLOWED"
            bad_plan = json.loads((SAMPLES / "settings.plan.json").read_text(encoding="utf-8"))
            bad_plan["steps"].append({"op": "run-shell", "command": "ignored"})
            bad_output = tmp_path / "bad.json"
            invalid = content(
                await session.call_tool(
                    "file_apply",
                    {"file": str(copied["settings.json"]), "plan": bad_plan, "output": str(bad_output)},
                )
            )
            assert invalid["code"] == "INVALID_STEP" and not bad_output.exists()

            eas_plan = json.loads((SAMPLES / "eas.plan.json").read_text(encoding="utf-8"))
            contract = content(await session.call_tool("eas_contract", {}))
            assert contract["format"] == "eas-hmi" and contract["plan_schema"]
            status = content(await session.call_tool("eas_status", {"project": str(state.root)}))
            assert status["revision"] == 0
            assert status["canonical_hash"] == eas_plan["base"]["semantic_hash"]
            query = content(await session.call_tool("eas_query", {"project": str(state.root)}))
            assert any(node["id"] == "CARD_A" for node in query["nodes"])
            inspected = content(
                await session.call_tool("eas_inspect", {"project": str(state.root), "object_id": "CARD_A"})
            )
            assert inspected["target"]["id"] == "CARD_A"
            context = content(
                await session.call_tool("eas_context", {"project": str(state.root), "object_id": "CARD_A"})
            )
            assert context["target"]["id"] == "CARD_A"
            before_head = state.head.read_bytes()
            preview = content(
                await session.call_tool("eas_preview", {"project": str(state.root), "plan": eas_plan})
            )
            assert preview["would_commit"] and preview["dry_run"]
            assert state.head.read_bytes() == before_head
            applied = content(await session.call_tool("eas_apply", {"project": str(state.root), "plan": eas_plan}))
            assert applied["committed"] and applied["revision"] == 1
            stale = content(await session.call_tool("eas_apply", {"project": str(state.root), "plan": eas_plan}))
            assert stale["code"] == "REVISION_CONFLICT"
            undone = content(await session.call_tool("eas_undo", {"project": str(state.root), "expected_revision": 1}))
            assert undone["committed"] and undone["revision"] == 2
            assert canonical_hash(state.load()) == eas_plan["base"]["semantic_hash"]

    asyncio.run(scenario())
