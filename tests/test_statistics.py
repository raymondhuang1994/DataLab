import pandas as pd
import pytest
from datalab.statistics import compare_groups


def test_welch_direction_ci_and_missing_records(tmp_path):
    frame = pd.DataFrame({"group": ["A"]*5+["B"]*5+[None], "value": [1,2,3,4,5,11,12,13,14,15,999]})
    source = tmp_path / "ab.csv"; frame.to_csv(source, index=False)
    result = compare_groups(source, "group", "value")
    assert result["effect"] == pytest.approx(10)
    assert result["mean_difference_ci"][0] > 0
    assert result["p_value"] < .01
    assert result["excluded_rows"] == 1


def test_constant_groups_not_claimed_significant(tmp_path):
    source = tmp_path / "ab.csv"
    pd.DataFrame({"group": ["A","A","B","B"], "value": [1,1,2,2]}).to_csv(source,index=False)
    with pytest.raises(ValueError, match="变异"):
        compare_groups(source,"group","value")
