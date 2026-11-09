"""Cross-runtime metric invariants and complete-data live dashboard behavior."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pandas as pd
import pytest
from datalab.dashboard import SourceStore, build_dashboard, _category_key, METRICS_ASSET
from datalab.metrics import normalize_metric, compute_metric, format_value
from test_dashboard import running_server, fetch, _bootstrap

ROWS = [
    {'date': '2026-01-01', 'channel': 'A', 'orders': 5, 'visits': 10, 'user': '001', 'status': 'valid'},
    {'date': '2026-01-02', 'channel': 'A', 'orders': 9, 'visits': 90, 'user': '001', 'status': 'valid'},
    {'date': '2026-01-01', 'channel': 'B', 'orders': 3, 'visits': 10, 'user': '002', 'status': 'valid'},
    {'date': '2026-01-02', 'channel': 'B', 'orders': None, 'visits': 100, 'user': None, 'status': 'valid'},
    {'date': '2026-01-03', 'channel': 'B', 'orders': 0, 'visits': 0, 'user': '003', 'status': 'valid'},
    {'date': '2026-01-03', 'channel': 'B', 'orders': 999, 'visits': 1, 'user': '004', 'status': 'excluded'},
]
RATIO = {'id': 'conversion', 'label': '转化率', 'aggregation': 'ratio', 'column': 'orders', 'denominator': 'visits', 'unit': 'percent', 'precision': 2, 'filters': [{'column': 'status', 'op': 'eq', 'value': 'valid'}]}


def source_csv(tmp_path, rows=ROWS):
    source = tmp_path / 'inputs' / '渠道 数据.csv'
    source.parent.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(source, index=False)
    return source


def js_result(rows, spec, formatted=None):
    binary = os.environ.get('NODE_BINARY') or shutil.which('node')
    if not binary:
        pytest.skip('Node required for Python/JavaScript contract tests (CI installs Node 20)')
    script = "const fs=require('fs'),m=require(process.argv[1]),p=JSON.parse(fs.readFileSync(0,'utf8')); try { process.stdout.write(JSON.stringify({result:m.computeMetric(p.rows,p.spec),formatted:p.formatted?.map(x=>m.formatValue(x.value,x.spec,x.preferences))})); }catch(e){ process.stdout.write(JSON.stringify({error:e.message}));}"
    result = subprocess.run([binary, '-e', script, str(METRICS_ASSET.resolve())], input=json.dumps({'rows': rows, 'spec': spec, 'formatted': formatted}, ensure_ascii=False), text=True, encoding="utf-8", capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('aggregation', ['sum','mean','median','ratio','weighted_mean','count_rows','count_nonmissing','count_distinct'])
def test_offline_js_and_python_share_values_filters_and_denominators(aggregation):
    spec = {'aggregation': aggregation, 'filters': RATIO['filters']}
    if aggregation != 'count_rows':
        spec['column'] = 'user' if aggregation.startswith('count_') else 'orders'
    if aggregation == 'ratio':
        spec['denominator'] = 'visits'
    if aggregation == 'weighted_mean':
        spec['weight'] = 'visits'
    spec = normalize_metric(spec)
    expected = compute_metric(pd.DataFrame(ROWS), spec)
    actual = js_result(ROWS, spec)['result']
    assert set(actual) == set(expected)
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value) if isinstance(value, (float,int)) else actual[key] == value


@pytest.mark.parametrize('op,value', [('eq','valid'),('ne','excluded'),('in',['valid']),('not_in',['excluded']),('is_null',None),('not_null',None)])
def test_filter_null_semantics_match(op, value):
    rows=[{'x':1,'status':'valid'},{'x':2,'status':None},{'x':3,'status':'excluded'}]
    predicate={'column':'status','op':op}
    if value is not None:
        predicate['value']=value
    spec=normalize_metric({'column':'x','aggregation':'sum','filters':[predicate]})
    assert js_result(rows,spec)['result']==compute_metric(pd.DataFrame(rows),spec)


@pytest.mark.parametrize('op', ['gt','ge','lt','le'])
def test_numeric_filter_semantics_match(op):
    rows=[{'x':1},{'x':2},{'x':None}]
    spec=normalize_metric({'column':'x','aggregation':'sum','filters':[{'column':'x','op':op,'value':1}]})
    assert js_result(rows,spec)['result']==compute_metric(pd.DataFrame(rows),spec)


def test_undefined_ratios_negative_weights_and_numeric_errors_match():
    spec=normalize_metric({'column':'x','denominator':'n','aggregation':'ratio'})
    for rows in ([{'x':1,'n':0}], [{'x':None,'n':5}], [{'x':None,'n':-1}], [{'x':'oops','n':2}], [{'x':'0x10','n':2}]):
        try:
            expected=compute_metric(pd.DataFrame(rows),spec)
        except ValueError:
            assert 'error' in js_result(rows,spec)
        else:
            assert js_result(rows,spec)['result']==expected
    spec=normalize_metric({'column':'x','aggregation':'sum'})
    rows=[{'x':1e16},{'x':1},{'x':-1e16}]
    assert js_result(rows,spec)['result']['value']==compute_metric(pd.DataFrame(rows),spec)['value']==1


def test_offline_and_python_display_use_same_rounding_currency_and_percent():
    cases=[{'value':value,'spec':{'unit':unit,'precision':precision,**({'currency':'CNY'} if unit=='currency' else {})}}
           for value in (2.675,1.005,-2.5,1234.567,None,-0.0) for unit in ('number','percent','currency') for precision in (0,2,4)]
    expected=[format_value(x['value'],x['spec']) for x in cases]
    actual=js_result([{'x':1}],normalize_metric({'column':'x'}),cases)['formatted']
    assert actual==expected


def test_registered_ratio_is_recomputed_after_filters_and_locks_semantics(tmp_path):
    source=source_csv(tmp_path)
    store=SourceStore(source,metric_definition=RATIO,date_column='date',group='channel',max_rows=2)
    full=store.query({})
    assert full['aggregate']['value']==pytest.approx(17/110)
    assert full['aggregate']['eligible_rows']==5
    assert full['detail_available']==2 and full['detail_total']==5
    assert full['detail_truncated'] is True and full['aggregation_scope']=='all_eligible_rows'
    assert full['formatted_value']=='15.45%'
    selected=store.query({'category':_category_key('A')})
    assert selected['aggregate']['value']==pytest.approx(14/100)
    day=store.query({'start':'2026-01-01','end':'2026-01-01'})
    assert day['aggregate']['value']==pytest.approx(8/20)
    zero=store.query({'start':'2026-01-03','end':'2026-01-03'})
    assert zero['aggregate']['value'] is None
    with pytest.raises(ValueError,match='锁定'):
        store.query({'agg':'sum'})
    with pytest.raises(ValueError):
        store.query({'expression':'__import__("os")'})


def test_live_full_data_aggregate_is_not_limited_by_browser_rows(tmp_path):
    source=source_csv(tmp_path,[{'date':'2026-01-01','sales':i,'region':'A'} for i in range(1,121)])
    with running_server(source,metric='sales',group='region',date_column='date',max_rows=7) as (_,base):
        initial=fetch(base)
        assert initial['aggregate']['value']==sum(range(1,121))
        assert len(initial['records'])==7 and initial['detail_available']==7
        assert initial['trend'][0]['value']==sum(range(1,121))
        assert initial['groups'][0]['value']==sum(range(1,121))
        request=Request(base+'/api/query',data=json.dumps({'agg':'count_distinct'}).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=3) as response:
            assert json.load(response)['aggregate']['value']==120
        bad=Request(base+'/api/query',data=b'{"path":"/etc/passwd"}',headers={'Content-Type':'application/json'},method='POST')
        with pytest.raises(HTTPError) as caught:
            urlopen(bad,timeout=3)
        assert caught.value.code==400
        external=Request(base+'/api/query',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://example.com'},method='POST')
        with pytest.raises(HTTPError) as caught:
            urlopen(external,timeout=3)
        assert caught.value.code==403


def test_count_compatibility_and_distinct_recompute(tmp_path):
    source=source_csv(tmp_path)
    rows=SourceStore(source,agg='count').query({})
    values=SourceStore(source,metric='user',agg='count').query({})
    distinct=SourceStore(source,metric_definition={'aggregation':'count_distinct','column':'user'},group='channel')
    assert rows['aggregate']['value']==6
    assert values['aggregate']['value']==5
    assert distinct.query({})['aggregate']['value']==4
    assert distinct.query({'category':_category_key('A')})['aggregate']['value']==1


def test_static_registered_definition_full_data_timezone_and_preferences(tmp_path):
    source=source_csv(tmp_path)
    output=tmp_path/'dashboard.html'
    build_dashboard(source,output,metric_definition=RATIO,date_column='date',group='channel',preferences={'display':{'precision':4},'dashboard':{'max_rows':6}})
    data=_bootstrap(output)[0]['data']
    assert data['metric_locked'] is True and len(data['records'])==6
    assert data['metric_definition']['precision']==2
    assert data['date_keys']['date']==[row['date'] for row in ROWS]
    assert '__METRICS_JS__' not in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError,match='不会截取'):
        build_dashboard(source,tmp_path/'oversize.html',metric_definition=RATIO,preferences={'dashboard':{'max_rows':5}})


def test_unsafe_browser_integers_are_rejected_and_ids_can_stay_text(tmp_path):
    source = tmp_path / 'inputs' /'large.json'
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps([{'amount':9007199254740993}]),encoding='utf-8')
    with pytest.raises(ValueError,match='安全整数'):
        build_dashboard(source,tmp_path/'bad.html',metric='amount')
    source.write_text(json.dumps([{'customer_id':'9007199254740993'},{'customer_id':'9007199254740992'}]),encoding='utf-8')
    build_dashboard(source,tmp_path/'ids.html',metric_definition={'aggregation':'count_distinct','column':'customer_id'})
    data=_bootstrap(tmp_path/'ids.html')[0]['data']
    assert js_result(data['records'],data['metric_definition'])['result']['value']==2


def test_live_oversized_aggregate_has_exact_label_and_precision_notice(tmp_path):
    source=source_csv(tmp_path,[{'amount':9007199254740991},{'amount':2}])
    store=SourceStore(source,metric='amount')
    data=store.snapshot()
    assert data['aggregate']['value']==9007199254740993
    assert data['formatted_value']=='9,007,199,254,740,993.00'
    assert '近似' in data['precision_notice']


def test_date_keys_are_shared_and_respect_project_timezone(tmp_path):
    source=source_csv(tmp_path,[{'date':'2026-01-01T23:00:00Z','sales':2},{'date':'2026-01-02T01:00:00Z','sales':3}])
    output=tmp_path/'dates.html'
    build_dashboard(source,output,metric='sales',date_column='date',timezone='Asia/Shanghai')
    data=_bootstrap(output)[0]['data']
    assert data['date_keys']['date']==['2026-01-02','2026-01-02']
    live=SourceStore(source,metric='sales',date_column='date',timezone='Asia/Shanghai').query({})
    assert [(item['key'],item['value']) for item in live['trend']]==[('2026-01-02',5)]


def test_project_monthly_frequency_matches_offline_and_live_keys(tmp_path):
    from datalab.cli import main
    rows=[{'date':'2026-01-31T23:00:00Z','channel':'A','orders':1,'visits':2,'status':'valid'},
          {'date':'2026-02-01T01:00:00Z','channel':'A','orders':9,'visits':98,'status':'valid'},
          {'date':'2026-03-01T01:00:00Z','channel':'A','orders':1,'visits':4,'status':'valid'}]
    source=source_csv(tmp_path,rows)
    project=tmp_path/'project.json'
    spec={key:value for key,value in RATIO.items() if key!='id'}
    project.write_text(json.dumps({'schema_version':'1.0','name':'Dashboard metric contract','data':{'date':'date'},'analysis':{'timezone':'Asia/Shanghai','frequency':'M','group':'channel'},'metrics':{'conversion':spec}}),encoding='utf-8')
    output=tmp_path/'project.html'
    assert main(['dashboard','--input',str(source),'--project',str(project),'--metric-id','conversion','--out',str(output)])==0
    data=_bootstrap(output)[0]['data']
    assert data['frequency']=='M' and data['timezone']=='Asia/Shanghai'
    assert data['period_keys']['date']==['2026-02','2026-02','2026-03']
    assert data['period_bounds']['2026-02']=={'start':'2026-02-01','end':'2026-02-28'}
    live=SourceStore(source,metric_definition=RATIO,date_column='date',freq='M',timezone='Asia/Shanghai').query({})
    assert [(item['key'],item['value']) for item in live['trend']]==[('2026-02',.1),('2026-03',.25)]
    assert live['trend'][0]['end']=='2026-02-28'


@pytest.mark.parametrize('extension', ['csv', 'json', 'parquet'])
def test_real_file_serialization_preserves_tiny_ratio_for_offline_js(tmp_path, extension):
    from datalab.io import load_frame
    source = tmp_path / 'inputs' / ('tiny.' + extension)
    source.parent.mkdir(exist_ok=True)
    rows = [{'x': 1.4e-15, 'y': 2e-15}]
    if extension == 'csv':
        pd.DataFrame(rows).to_csv(source, index=False)
    elif extension == 'json':
        source.write_text(json.dumps(rows), encoding='utf-8')
    else:
        pytest.importorskip('pyarrow')
        pd.DataFrame(rows).to_parquet(source, index=False)
    spec = normalize_metric({'aggregation': 'ratio', 'column': 'x', 'denominator': 'y'})
    expected = compute_metric(load_frame(source), spec)
    output = tmp_path / 'tiny.html'
    build_dashboard(source, output, metric_definition=spec)
    data = _bootstrap(output)[0]['data']
    assert data['records'][0]['x'] == load_frame(source).iloc[0]['x']
    actual = js_result(data['records'], data['metric_definition'])['result']
    assert actual['value'] == pytest.approx(expected['value']) == pytest.approx(.7)


@pytest.mark.parametrize('infinity', [float('inf'), float('-inf')])
def test_actual_numeric_infinity_refused_before_serializing_count(tmp_path, infinity):
    pytest.importorskip('pyarrow')
    source = tmp_path / 'inputs' / 'nonfinite.parquet'
    source.parent.mkdir(exist_ok=True)
    pd.DataFrame({'x': [1.0, float('nan')]}).to_parquet(source, index=False)
    spec = {'aggregation': 'count_nonmissing', 'column': 'x'}
    store = SourceStore(source, metric_definition=spec)
    initial = store.snapshot()
    assert initial['aggregate']['value'] == 1
    pd.DataFrame({'x': [1.0, infinity]}).to_parquet(source, index=False)
    output = tmp_path / 'rejected.html'
    with pytest.raises(ValueError, match='非有限'):
        build_dashboard(source, output, metric_definition=spec)
    assert not output.exists()
    assert store.refresh(force=True) is False
    stale = store.snapshot()
    assert stale['status']['stale'] is True and '非有限' in stale['status']['error']
    assert stale['records'] == initial['records']
    assert stale['aggregate']['value'] == 1


def test_infinity_text_and_real_missing_values_keep_count_meaning(tmp_path):
    source = tmp_path / 'inputs' / 'text.json'
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps([{'x': 'Infinity'}, {'x': '-Infinity'}, {'x': None}]), encoding='utf-8')
    output = tmp_path / 'text.html'
    spec = normalize_metric({'aggregation': 'count_nonmissing', 'column': 'x'})
    build_dashboard(source, output, metric_definition=spec)
    data = _bootstrap(output)[0]['data']
    assert data['records'] == [{'x': 'Infinity'}, {'x': '-Infinity'}, {'x': None}]
    assert js_result(data['records'], data['metric_definition'])['result']['value'] == 2


@pytest.mark.parametrize('groups', [[1, '1'], [True, 1], ['(缺失)', None]])
def test_mixed_or_ambiguous_groups_rejected_and_not_offered_offline(tmp_path, groups):
    source = tmp_path / 'inputs' / 'typed-groups.json'
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps([{'segment_id': value, 'channel': 'valid', 'revenue': 10} for value in groups]), encoding='utf-8')
    with pytest.raises(ValueError, match='混合|缺失'):
        build_dashboard(source, tmp_path / 'bad.html', metric='revenue', group='segment_id')
    assert not (tmp_path / 'bad.html').exists()
    output = tmp_path / 'safe.html'
    build_dashboard(source, output, metric='revenue', group='channel')
    data = _bootstrap(output)[0]['data']
    invalid = next(column for column in data['columns'] if column['name'] == 'segment_id')
    assert invalid['groupable'] is False and invalid['group_error']
    assert 'segment_id' not in data['group_labels']
    assert next(column for column in data['columns'] if column['name'] == 'channel')['groupable'] is True
    store = SourceStore(source, metric='revenue', group='channel')
    assert store.snapshot()['aggregate']['value'] == 20
    with pytest.raises(ValueError, match='混合|缺失'):
        store.query({'group': 'segment_id'})


def test_homogeneous_numeric_group_labels_are_shared_with_report(tmp_path):
    from datalab.grouping import group_labels
    from datalab.io import load_frame
    source = tmp_path / 'inputs' / 'numeric-groups.json'
    source.parent.mkdir(exist_ok=True)
    source.write_text(json.dumps([{'segment_id': 1, 'revenue': 10}, {'segment_id': 1.0, 'revenue': 20}, {'segment_id': None, 'revenue': 7}]), encoding='utf-8')
    output = tmp_path / 'numeric.html'
    build_dashboard(source, output, metric='revenue', group='segment_id')
    data = _bootstrap(output)[0]['data']
    assert data['group_labels']['segment_id'] == group_labels(load_frame(source)['segment_id']).tolist() == ['1', '1', '(缺失)']
    live = SourceStore(source, metric='revenue', group='segment_id').query({})
    assert {row['label']: row['value'] for row in live['groups']} == {'1': 30, '(缺失)': 7}
    assert [row['label'] for row in live['categories']] == ['(缺失)', '1']
