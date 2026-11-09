import json
import pandas as pd
import pytest
from datalab.analysis import analyze
from datalab.grouping import group_labels
from datalab.validation import validate_bundle


def test_mixed_number_text_categories_are_rejected_before_writing(tmp_path):
    source=tmp_path/'typed.json'
    source.write_text(json.dumps([{'segment_id':1,'amount':10},{'segment_id':'1','amount':20}]),encoding='utf-8')
    with pytest.raises(ValueError,match='混合'):
        analyze(source,tmp_path/'out',metric='amount',group='segment_id',charts=False)
    assert not (tmp_path/'out').exists()


def test_numeric_and_boolean_group_labels_have_explicit_consistent_rules(tmp_path):
    assert group_labels(pd.Series([1,1.0,2.5])).tolist()==['1','1','2.5']
    assert group_labels(pd.Series([True,False,None])).tolist()==['true','false','(缺失)']
    source=tmp_path/'numbers.json'
    source.write_text(json.dumps([{'segment':1,'amount':10},{'segment':1.0,'amount':20}]),encoding='utf-8')
    result=analyze(source,tmp_path/'out',metric='amount',group='segment',charts=False)
    assert next(t for t in result['tables'] if t['id']=='by_group')['rows']==[['1',30,2]]
    assert validate_bundle(tmp_path/'out/result.json')['checks_status']=='passed'
