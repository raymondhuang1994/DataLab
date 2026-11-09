# 看板：共享指标、完整快照与全量实时聚合

`dashboard` 导出可独立打开的离线 HTML；`serve` 在当前终端启动本机服务，轮询指定文件。两者均不依赖 CDN 或云端上传。先确认指标口径、分组、时区和趋势周期。

离线 HTML 必须写到原始数据目录之外的新文件；再次导出使用新文件名。已有 HTML、项目和偏好配置均不能覆盖。`serve` 只读源文件，刷新数据应由另一个明确授权的流程负责；它不是 DataLab 修改原数据的例外。详见 [只读输入与安全输出](safety.md)。

## 使用项目中注册的指标

报告与看板应选择同一项目 JSON 中的 `metric-id`，而不是分别手写转化率或平均值。例如项目包含：

```json
{
  "schema_version": "1.0",
  "name": "渠道指标项目",
  "data": {"date": "date"},
  "analysis": {"group": "channel", "timezone": "Asia/Shanghai", "frequency": "M"},
  "metrics": {
    "conversion": {
      "label": "转化率", "aggregation": "ratio", "column": "orders",
      "denominator": "visits", "unit": "percent", "precision": 2,
      "filters": [{"column": "status", "op": "eq", "value": "valid"}]
    }
  }
}
```

```text
python scripts/run.py dashboard --input inputs/sales.csv --project config/project.json --metric-id conversion --preferences config/preferences.json --out results/dashboard-001.html
python scripts/run.py serve --input inputs/sales.csv --project config/project.json --metric-id conversion --preferences config/preferences.json --port 8765
```

命令相对于 Skill 目录执行；使用当前环境的解释器。多表 XLSX 要指定 `--sheet`。CSV/TSV、XLSX、JSON对象数组、JSONL/NDJSON及Parquet使用同一文件读取器；Parquet需可选依赖。

注册指标的列、过滤条件、分子/分母、权重、去重定义和聚合方式会锁定；界面只能改变分组、类别和日期范围，不允许将一个注册比例切成求和。筛选后重新计算合计比率、共同有效样本的加权平均或去重数。缺失值和零分母保持未定义，不变成零。

分组与报告使用同一套规范标签。一个分组列的非空值必须同为文本、数值、布尔或日期；数值 `1` 与文本 `"1"` 混在同列时明确拒绝，不自动合并成同名类别。数值 `1.0` 显示为 `1`；缺失显示为 `(缺失)`，若该字面标签本身也出现在数据中则拒绝以避免冲突。不合法的分组列不会出现在离线分组选择框中，实时查询也会校验。

支持 `sum`、`mean`、`median`、`ratio`、`weighted_mean`、`count_rows`、`count_nonmissing`、`count_distinct`。过滤条件是白名单数据结构，支持 `eq/ne/in/not_in/is_null/not_null/gt/ge/lt/le`，多条件为 AND；不接受表达式、SQL或代码。

## 兼容未注册的单字段分析

```text
python scripts/run.py dashboard --input inputs/sales.csv --metric revenue --group region --date date --agg sum --out results/revenue-001.html
python scripts/run.py serve --input inputs/sales.csv --metric revenue --group region --date date --agg sum --interval 5 --port 8765
```

旧 `count` 在指定 `--metric` 时映射为非空值计数；不指定字段时映射为行数，与分析模块一致。明确写 `count_rows` 时不传字段，去重使用 `count_distinct`。未注册字段仍可切换，文本字段只适合计数/去重。率名称的旧默认仍是未加权平均并明确提示；整体率必须改用注册的分子/分母指标。

## 全量与浏览器容量

- **离线模式**：必须携带全部源记录才能支持任意筛选。默认最多50,000行；超出就拒绝导出，并提示预聚合、提高 `--max-rows` 或使用实时服务。不会截取前几行后称为总体。
- **实时模式**：整个源表保留在Python进程；每次查询调用共享 `compute_metric` 对完整合格数据计算KPI、趋势和分组。浏览器仅取得聚合结果和当前明细页，`--max-rows` 只限制可浏览明细，绝不限制指标计算范围。
- 明细每页50行，超出明细上限时显示“汇总与图表使用全部数据、明细限量”。分组图最多显示前12类；完整类别仍可在筛选框选择。趋势超过200点时减少可点击标记，线条保留全部聚合点。
- 当前实现仍在Python内存中读取整个源文件，刷新时全量重读，没有增量查询/生产大数据引擎。高基数分组和很大的源表仍应在上游按明确口径预聚合。

## 时间和展示设置

`--project` 读取项目的日期列、分组、时区和趋势周期；注册项目的默认周期来自同一分析设置。`--timezone` 和 `--freq D/W/M/Q/Y` 可显式覆盖。未使用项目的旧调用默认UTC、按日汇总。

Python使用与分析报告相同的日期解析来生成规范日键/周期键。离线页面直接使用这些键，避免浏览器日期解析差异；实时过滤也在Python执行。页面展示实际时区和周期；点击趋势点筛选该周期的完整起止日。日期无效记录不进趋势，并显示数量；日期筛选使用所配置时区的日边界。

`--preferences` 读取 `dashboard.interval`（默认5秒）、`dashboard.max_rows`（默认50000）、`display.precision`（默认2）、`display.missing_label`、`charts.color`、`report.title`。显式CLI值优先；指标自身precision优先于显示偏好。比例以百分比展示，币种以代码加金额展示，计算值保持原精度。偏好不改变指标业务口径。

浏览器不能安全表示绝对值超过 `2^53−1` 的整数。源表存在此类数值会明确拒绝：标识符应保持字符串，数值指标应先按明确单位换算。实时聚合若产生超安全范围的整数，其数值标签由Python精确格式化，并明确提示图形坐标为近似；离线聚合超范围会报错，不输出悄悄舍入的数值。

载荷使用Python浮点数的可往返JSON表示，不先截成15位小数；微小值也必须与报告计算一致。实际数值 `Infinity/-Infinity` 会拒绝导出或拒绝更新实时快照，要求先按明确方法处理，避免JSON将它们转成空值后改变非空计数。正常NaN/空值仍作为缺失；文本字符串 `"Infinity"` 保持文本，非空计数时不会自动改成缺失。

## 刷新与失败恢复

服务仅绑定 `127.0.0.1` / `localhost`，默认端口8765；`--port 0`可自动选择空闲端口。最短轮询2秒，默认5秒；文件检查和浏览器状态查询各需时间，变更通常在一到两个周期内显示。`Ctrl+C`或关闭进程会停止，不安装后台服务。

“最近检查”“最近成功读取”“数据变更”分别记录不同事件。源文件格式损坏、删除、字段不符合指标时，保留上一份完整可用快照并标记过期；恢复后继续更新。读取前后指纹核对，避免读取一半写入的文件。浏览器连接失败会显示提示。

长驻服务预期会观察到外部更新；源文件指纹变化本身不能证明是 DataLab 写入，也不应被描述为整个服务生命周期内源文件固定不变。这里的只读承诺指 DataLab 只读取源文件。需要防止任意代码写入时，由宿主为服务进程提供只读输入权限。

GET `/api/data`只读取指定源快照；POST `/api/query`仅接受指标/聚合、分组、类别、日期起止、页码这些控件，验证本机Host/Origin、JSON类型、体积和字段白名单。没有任意路径、任意查询或执行代码接口。

数据库/API/MCP数据仍需上游授权流程导出到指定文件。此服务不连接数据库、保存凭证、自动重启或部署云端。宿主无法启动持续进程时交付离线快照，并说明边界。

## 验证

实时Python与离线纯JavaScript使用同一组跨语言fixture检验比例、加权、去重、空值、过滤、零分母、负权重、错误数字、显示舍入和币种。HTTP测试覆盖全量聚合/明细限量、受控查询、更新、损坏和恢复。注册指标界面应实际检查：指标和聚合被锁定、类别/日期改变后结果正确、超明细上限不改变总体。
