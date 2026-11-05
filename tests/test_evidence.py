import json

from nightwalker.evidence import EvidenceStore


def test_evidence_store_writes_task_scoped_json(tmp_path):
    store = EvidenceStore(str(tmp_path))
    evidence_id = store.save("task-123", {"title": "Example", "text": "Visible page text"})

    record = json.loads((tmp_path / f"{evidence_id}.json").read_text(encoding="utf-8"))
    assert record["evidence_id"] == evidence_id
    assert record["task_id"] == "task-123"
    assert record["payload"]["title"] == "Example"
