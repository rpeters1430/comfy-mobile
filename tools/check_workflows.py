"""Run every workflow through conversion and validation against an object_info snapshot.

Usage: python tools/check_workflows.py [object_info.json] [workflow dirs...]
Fetch a snapshot with: curl -o fixtures/object_info.local.json http://<comfy>:8188/object_info
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.workflow_conversion import convert_ui, ConversionError
from app.workflow_validation import inspect_workflow

ROOT = Path(__file__).resolve().parents[1]


def check(path, info):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("nodes"), list):
        try:
            data = convert_ui(data, info)
        except ConversionError as exc:
            return False, f"conversion: {exc}"
    result = inspect_workflow(data, info)
    problems = result["errors"] + result["warnings"]
    return result["runnable"], problems[0] if problems else ""


def main():
    info_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "fixtures" / "object_info.local.json"
    dirs = [Path(d) for d in sys.argv[2:]] or [ROOT / "workflows"]
    info = json.loads(info_path.read_text(encoding="utf-8"))
    passed = total = 0
    for directory in dirs:
        for path in sorted(directory.rglob("*.json")):
            ok, reason = check(path, info)
            total += 1
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'}  {path.name[:48]:48}  {reason}")
    print(f"\n{passed}/{total} runnable")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
