from pathlib import Path

import pytest

from scripts.import_jddc import normalize_rows


ROOT = Path(__file__).resolve().parent.parent


def test_jddc_importer_maps_only_selected_split(tmp_path):
    source = tmp_path / "jddc.tsv"
    source.write_text(
        "conversation_id\tquery\tintent\tsplit\n"
        "c1\t我的订单到哪了\t查物流\ttest\n"
        "c2\t我要退款\t申请退款\ttrain\n"
        "c3\t退回的钱没到账\t退款进度\ttest\n",
        encoding="utf-8",
    )
    records = normalize_rows(
        source,
        {"查物流": "logistics", "申请退款": "refund", "退款进度": "refund_status"},
    )
    assert [item["expected_intent"] for item in records] == ["logistics", "refund_status"]
    assert records[0]["tags"] == ["external", "jddc", "查物流"]


def test_jddc_importer_rejects_unknown_target_intent(tmp_path):
    source = tmp_path / "jddc.tsv"
    source.write_text("conversation_id\tquery\tintent\tsplit\nc1\t测试\t源标签\ttest\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported CommerceMind intents"):
        normalize_rows(source, {"源标签": "invented_intent"})


def test_jddc_policy_does_not_bundle_licensed_dialogue():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/eval/external_jddc*.jsonl" in gitignore
    attribution = (ROOT / "data/eval/JDDC_ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "does not redistribute" in attribution
