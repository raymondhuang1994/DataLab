import json
from datalab.analysis import analyze
from datalab.validation import validate_bundle


def test_evidence_sentence_can_be_inside_narrative_paragraph(tmp_path):
    source=tmp_path / "data.csv"
    source.write_text("orders,visits\n1,10\n",encoding="utf-8")
    out=tmp_path / "result"
    data=analyze(source,out,metric_definition={"label":"整体率{含测试}","aggregation":"ratio","column":"orders","denominator":"visits","unit":"percent"},charts=False)
    original=data['claims'][0]['text']
    data['summary']=['以下结果仅描述观测样本。'+original+'还需确认业务归因。']
    path=out/'result.json'
    path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    assert validate_bundle(path)['checks_status']=='passed'
    data['summary']=['以下结果仅描述观测样本。整体率为99%。还需确认业务归因。']
    path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    assert validate_bundle(path)['status']=='failed'
