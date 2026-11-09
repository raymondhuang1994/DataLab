import pytest
from datalab.dashboard import SourceStore, build_dashboard
from datalab.cli import main
import json


def test_live_project_quality_failure_preserves_last_valid_snapshot(tmp_path):
    source=tmp_path/'source.csv'
    source.write_text('id,amount\na,10\nb,20\n',encoding='utf-8')
    store=SourceStore(source,metric='amount',quality_rules={'unique_key':['id']})
    assert store.snapshot()['aggregate']['value']==30
    source.write_text('id,amount\na,10\na,10\n',encoding='utf-8')
    store.refresh(force=True)
    stale=store.snapshot()
    assert stale['aggregate']['value']==30 and stale['status']['stale']
    assert '主键' in stale['status']['error']
    source.write_text('id,amount\na,10\nb,25\n',encoding='utf-8')
    store.refresh(force=True)
    assert store.snapshot()['aggregate']['value']==35
    assert not store.snapshot()['status']['stale']


def test_offline_cli_honors_project_quality_rules(tmp_path):
    source=tmp_path/'source.csv'
    source.write_text('id,amount\na,10\na,10\n',encoding='utf-8')
    project=tmp_path/'project.json'
    project.write_text(json.dumps({'schema_version':'1.0','name':'Orders','metrics':{'revenue':{'aggregation':'sum','column':'amount'}},'quality':{'unique_key':['id']}}),encoding='utf-8')
    output=tmp_path/'bad.html'
    assert main(['dashboard','--input',str(source),'--project',str(project),'--metric-id','revenue','--out',str(output)])==2
    assert not output.exists()
