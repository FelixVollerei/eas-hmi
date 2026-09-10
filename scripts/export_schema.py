"""Export the authoritative Pydantic schema as a reviewable JSON Schema artifact."""

import json
from pathlib import Path

from eas_hmi.model import Project

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "schemas/project.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(Project.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(path)
