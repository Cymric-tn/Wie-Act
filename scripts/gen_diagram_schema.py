"""Regenerates docs/diagram.schema.json from the pydantic models: python -m scripts.gen_diagram_schema"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "app"))
from diagrams.spec import Diagram  # noqa: E402

out = ROOT / "docs" / "diagram.schema.json"
out.write_text(json.dumps(Diagram.model_json_schema(by_alias=True), indent=2, ensure_ascii=False), encoding="utf-8")
print("wrote", out)
