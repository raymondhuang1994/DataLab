from datalab.analysis import analyze
from datalab.charts import chart_table


def test_tiny_changes_are_not_magnified_below_display_resolution(tmp_path):
    table={'id':'trend','title':'Margin','source_file':'tables/trend.csv','columns':['period','value'],
           'rows':[['Jan',.31],['Feb',.310000088],['Mar',.31]]}
    output=chart_table(table,tmp_path,'trend','line',metric_spec={'unit':'percent','precision':1})
    low,high=output['value_axis_limits']
    assert high-low >= .003999
    assert low < .31 < high
    ticks=output['value_axis_tick_labels']
    assert len(ticks)==len(set(ticks))
    assert (tmp_path/'trend.png').is_file()


def test_rounded_ties_do_not_generate_false_unique_leader(tmp_path):
    source=tmp_path/'data.csv'
    source.write_text('region,profit,revenue\nA,31,100\nB,31.0000001,100\n',encoding='utf-8')
    result=analyze(source,tmp_path/'out',group='region',charts=False,
                   metric_definition={'aggregation':'ratio','column':'profit','denominator':'revenue','unit':'percent','precision':1})
    assert '相同' in result['findings'][0]['title']
    assert '不能凭舍入' in result['findings'][0]['text']
    values=next(table['rows'] for table in result['tables'] if table['id']=='by_group')
    assert values[0][1] != values[1][1]
