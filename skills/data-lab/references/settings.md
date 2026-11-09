# 可复用项目口径与展示偏好

重复分析同一业务数据时，用显式项目 JSON 保存指标、粒度和质量要求；用另一个偏好 JSON 保存报告外观。两者均为 `schema_version: "1.0"`。不扫描用户目录，不寻找未指定的配置或数据文件，不把连接凭据写入其中。数据来源仍由本次任务明确提供。

项目和偏好文件必须是 UTF-8 JSON。未知字段、重复 JSON 键、非法组合及 NaN/Infinity 会报错；不要依赖拼错的字段被忽略。

## 项目结构

`name` 是必填名称。其他部分可以省略：

| 部分 | 字段与作用 |
|---|---|
| `data` | `grain`：一行代表什么，可用描述或字段列表；`entity_key`：实体键，字段名或列表；`date`：日期列；`sheet`：显式工作表名或从0开始的索引 |
| `analysis` | `timezone`：IANA时区；`frequency`：D/W/M/Q/Y；`group`：分组列 |
| `metrics` | 指标 ID 到指标定义的映射。必须显式选择 ID，不自动选择唯一指标 |
| `quality` | `required_columns`：必需字段；`unique_key`：不得缺失或重复的复合键；`max_missing_fraction`：逐列阈值映射，每值0—1；`min_rows`：非负最小源行数 |

`data.entity_key` 传给画像的 `key` 参数；`quality.unique_key` 是数据质量要求，两者不要随意互换。质量要求面向完整源数据，指标过滤不能隐藏原始数据的质量问题。省略质量项或空键列表表示没有配置该项要求。

最小项目例子：

```json
{
  "schema_version": "1.0",
  "name": "渠道分析",
  "data": {"grain": ["month", "channel"], "date": "month"},
  "analysis": {"group": "channel", "timezone": "Asia/Shanghai", "frequency": "M"},
  "metrics": {
    "conversion": {
      "label": "整体转化率", "aggregation": "ratio",
      "column": "orders", "denominator": "visits",
      "unit": "percent", "precision": 2
    }
  },
  "quality": {"required_columns": ["orders", "visits"], "max_missing_fraction": {"visits": 0}}
}
```

仓库的 `examples/project.json` 和 `examples/preferences.json` 配合合成销售数据 `examples/sales.csv` 使用；它们不是自动读取的全局配置。

## 指标定义

允许字段为 `id`、`label`、`aggregation`、`column`、`denominator`、`weight`、`filters`、`unit`、`currency`、`precision`。注册表的键就是指标 ID；定义中的 `id` 若填写，必须一致。

| aggregation | 计算口径 |
|---|---|
| `sum` / `mean` / `median` | 数值列的和、均值、中位数；空数据和全缺失结果为未定义 |
| `ratio` | 共同有效行的分子之和 / 分母之和；需要 `column` 与 `denominator` |
| `weighted_mean` | 共同有效行的数值×权重之和 / 权重之和；需要 `column` 与 `weight` |
| `count_rows` | 过滤后行数；禁止同时指定 `column` |
| `count_nonmissing` | 指定列的非缺失记录数 |
| `count_distinct` | 指定列排除缺失后的去重值数 |

其他聚合禁止携带 `weight` 或 `denominator`。旧命令的 `count` 有指标列时转换为 `count_nonmissing`，没有指标列时转换为 `count_rows`；规范定义不使用含糊的 `count`。

比例和加权计算只使用分子、分母/权重共同有效的记录。过滤后的负分母、负权重直接报错，即使该行分子缺失；零分母/零权重总和得到未定义。数值无穷值按无效值排除；非法数字文本（如 `5%`）报错，不擅自缩放或去除单位。计数的0具有实际意义；不得把未定义数值也填成0。

计算函数返回 `input_rows`（过滤前）、`eligible_rows`（过滤后）、`valid_rows`（本指标有效行）和 `excluded_rows`（过滤后因缺失/非有限值排除的行）。`count_distinct.valid_rows` 是非缺失输入行数，**不是**去重结果值。

分组字段应采用明确的一种类别类型。数字 `1` 和文本 `"1"` 不会被静默合并：混合类型会要求先明确转换。纯数字的 `1` 与 `1.0` 使用同一类别标签；缺失类别保留为 `(缺失)`，与实际字面标签冲突时明确报错。

## 过滤条件

`filters` 是条件列表，条件之间为 AND。每项形如 `{"column":"status","op":"eq","value":"valid"}`。只允许 `eq/ne/in/not_in/is_null/not_null/gt/ge/lt/le`；不接受表达式、代码或任意函数。

`in/not_in` 的 value 是非空值标量组成的列表，空列表也具有正常集合语义。`is_null/not_null` 不带 value。其余条件必须提供非空的有限标量。比较不隐式转换类型；包括 `ne/not_in` 在内的普通比较都排除缺失。需要选择缺失时显式使用 `is_null`。在直接传入 DataFrame 时，空字符串是文本值，None/NaN/pd.NA才是缺失；标准文件加载器已把空单元转换成缺失。

## 展示偏好

偏好文件只接受以下字段，不允许指标或分析口径：

```json
{
  "schema_version": "1.0",
  "report": {"title": "DataLab 数据分析报告", "page_size": "A4", "cjk_font": null},
  "charts": {"color": "#286B85"},
  "display": {"precision": 2, "missing_label": "—"},
  "dashboard": {"interval": 5, "max_rows": 50000}
}
```

上述是程序默认值。页纸为 A4/Letter；中文字体为目标环境已有字体名或 null；颜色为六位 HEX；精度是0—12的整数；刷新间隔至少2秒；最大展示行数为正整数。它们不改变汇总的数据范围或计算值。

指标 `unit` 只能是 `number`、`percent`、`currency`，默认 number。percent 显示时乘100并附 `%`；currency 需要大写三字母代码，显示如 `CNY 1,234.50`。计数指标使用 number。所有格式均使用千位分隔和固定小数位。指标显式 `precision` 优先，否则使用偏好的 display.precision；底层值不四舍五入。缺失展示用 missing_label，不覆盖源数据。

## 解析顺序与复现

显式非 None 的调用覆盖项 > 项目设置 > 程序默认。title 可由 report.title 提供，显式 title 仍优先；偏好永远不改变字段、分母、权重、聚合或过滤。选择注册指标时，同时传入 metric/agg/weight/denominator/filters 会报冲突，即使相同也不默默覆盖。分组、日期、时区及展示精度仍可显式覆盖。

Python 接口：

```python
from datalab.settings import resolve_settings
resolved = resolve_settings(
    project_path="project.json", preferences_path="preferences.json",
    metric_id="conversion", overrides={"group": "region"},
)
# metric_definition / analysis / preferences / quality / provenance
```

analysis 使用现有参数名 metric/group/date/agg/weight/denominator/sheet/key/timezone/freq/title；过滤条件保存在 metric_definition。无显式指标时保留画像模式，metric_definition 为 None。`provenance.files` 记录已读取文件的路径与 SHA256，`field_sources` 记录最终字段来自默认、项目、偏好还是显式覆盖。

结果及复算配置应保存实际解析后的定义、偏好和来源记录。复跑已有分析时使用保存的设置快照，不因项目文件后来变化而悄悄更改原口径；要采用新设置，应明确启动新的分析。
