"""Run every workflow through conversion and validation against an object_info snapshot.

Usage: python tools/check_workflows.py [object_info.json] [workflow dirs...]
       python tools/check_workflows.py --trim [object_info.json]
Fetch a snapshot with: curl -o fixtures/object_info.local.json http://<comfy>:8188/object_info

--trim writes fixtures/object_info.bundled.json for the CI test in tests/test_bundled_workflows.py. The repository
is public, so it keeps only the node classes the bundled workflows use, and drops every file-like option (models,
LoRAs, input images) except names the bundled workflows already contain. Nothing about the PC that isn't already
in workflows/ ends up in git.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.settings_check import check as check_settings
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
    settings_issues = [f"settings_check: {i['text']}" for i in check_settings(data, {})]
    problems = result["errors"] + result["warnings"] + settings_issues
    return result["runnable"] and not settings_issues, problems[0] if problems else ""


FILE_LIKE = re.compile(r"[/\\]|\.[A-Za-z0-9]{2,12}$")


def _base(name: str) -> str:
    return re.split(r"[/\\]", name)[-1].casefold()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _node_types(graph: dict):
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("type"), str):
            yield node["type"]
    for sub in (graph.get("definitions") or {}).get("subgraphs") or []:
        yield from _node_types(sub)


def _filter_options(value, used: set):
    """Drop file-like strings not named in the bundled workflows from every list of strings."""
    if isinstance(value, list):
        if value and all(isinstance(v, str) for v in value):
            return [v for v in value if not FILE_LIKE.search(v) or _base(v) in used]
        return [_filter_options(v, used) for v in value]
    if isinstance(value, dict):
        return {k: _filter_options(v, used) for k, v in value.items()}
    return value


def trim(info: dict, workflows: list[dict]) -> dict:
    used = {_base(s) for wf in workflows for s in _strings(wf) if FILE_LIKE.search(s)}
    kinds = {k for wf in workflows for k in _node_types(wf)}
    return {k: _filter_options(v, used) for k, v in sorted(info.items()) if k in kinds}


def main():
    if sys.argv[1:2] == ["--trim"]:
        info_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "fixtures" / "object_info.local.json"
        workflows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "workflows").glob("*.json"))]
        out = ROOT / "fixtures" / "object_info.bundled.json"
        out.parent.mkdir(exist_ok=True)
        trimmed = trim(json.loads(info_path.read_text(encoding="utf-8")), workflows)
        out.write_text(json.dumps(trimmed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out.relative_to(ROOT)} ({len(trimmed)} node classes)")
        return 0
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
