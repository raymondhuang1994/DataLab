# DataLab v0.2 使用与升级

本版优先解决指标口径、验证覆盖和可复用设置。仍是 Skill 与执行工具，不提供设置网页或托管后台服务。

## 直接对助手说

> 使用 DataLab，读取这份数据和 project.json，按其中的 conversion 指标分析渠道表现。采用 preferences.json 的显示偏好。生成图表、HTML 和 Word，说明每类检查完成了什么，保留可复算结果。

> 用同一份项目配置和指标建立刷新看板。筛选后的转化率重新按分子与分母计算，报告和看板保持相同口径。

项目配置负责“怎么算”：字段、分母、权重、业务过滤、时区、汇总周期、复合主键及缺失阈值。用户偏好负责“怎么显示”：标题、百分比精度、颜色、Word纸张/字体和看板运行参数。偏好不能改写业务指标。

## 示例命令

先按 INSTALL.md 准备环境。下方 `python` 在 Windows 替换为 `.venv\Scripts\python.exe`，macOS替换为 `.venv/bin/python`；从 DataLab 根目录运行。所有路径都可以带中文或空格，但在终端参数中应完整加引号。

```text
python skills/data-lab/scripts/run.py config --project examples/project.json --preferences examples/preferences.json --metric-id gross_margin
python skills/data-lab/scripts/run.py analyze --input examples/sales.csv --project examples/project.json --preferences examples/preferences.json --metric-id gross_margin --out gross-margin-run
python skills/data-lab/scripts/run.py word --bundle gross-margin-run/result.json --out gross-margin-run/report.docx
python skills/data-lab/scripts/run.py dashboard --input examples/sales.csv --project examples/project.json --preferences examples/preferences.json --metric-id gross_margin --out gross-margin-dashboard.html
python skills/data-lab/scripts/run.py serve --input examples/sales.csv --project examples/project.json --preferences examples/preferences.json --metric-id gross_margin
```

`config` 只读并显示最终设置及其来源。系统不会自动选择唯一指标，也不自动搜索电脑上的配置文件。注册指标与命令行公式覆盖同时出现时会明确报错，避免名义相同而公式变化。

## 计数与比例

| 需要的指标 | 对应定义 |
|---|---|
| 所有记录，包括字段缺失的记录 | count_rows |
| 指定字段已填的记录数 | count_nonmissing |
| 独立客户、订单等 | count_distinct，并指定对应键字段 |
| 总转化率 | ratio，指定成交分子与访问分母 |
| 按人数合并人均值 | weighted_mean，指定人数权重 |

零分母及没有有效数值的结果保持未定义。计数0本身有意义。含缺失的比例/加权数据使用共同有效记录；需要不同缺失口径时应先明确业务定义并准备相应数据，不能只在显示层补零。

## 怎样理解验证结果

`checks_status` 说明已执行自动检查是否通过；`status` 说明整份分析仍需哪些审阅。正常计算完成的结果通常是：

```json
{"status":"needs_review","checks_status":"passed"}
```

这不表示程序没运行，而是诚实区分算术与专业判断。验证器会复算支持的总体、分组、周期指标，核对源指纹、质量规则及结构化数字声明。自由叙述、因果解释、模型假设和版式不能由数字匹配自动保证。请查看 `coverage` 中已检查与未检查的范围。

结构化 `claims` 把正文中的关键数字与计算证据关联。修订正文时应同时维护声明和引用；完整声明句可以放在自然段落中，不要求单独占一个段落。仅修改JSON和CSV让两者一致，仍可能被源数据独立复算发现。

## 复现和旧版迁移

`config.json` 保存实际生效的定义和偏好。`rerun` 使用这一快照；项目配置之后被修改不会悄悄改变旧分析。要采用新口径，请使用新配置重新执行 analyze。

旧schema 1.0仍可读取，新分析输出schema 1.1。自动化脚本原来判断 `status == "passed"` 的地方，应改看 `checks_status` 并保留整体审阅状态。看板旧count有metric时改为非空计数；需要全行计数请显式使用count_rows。

Windows/macOS共用Python模块、JSON配置与HTML资源；Word字体按系统选择，并可指定已有字体。CI包含两个操作系统与三个Python版本，但本地通过不等于远程CI或Windows实机已通过，实际验证范围见 VALIDATION.md。
