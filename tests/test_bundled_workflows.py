import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_workflows():
    files=list((ROOT/'workflows').glob('*.json'))
    assert len(files)==32
    for path in files:
        obj=json.loads(path.read_text())
        assert isinstance(obj.get('nodes'),list)
        assert isinstance(obj.get('links'),list)
        ids={str(n['id']) for n in obj['nodes']}
        assert len(ids)==len(obj['nodes'])
