# 建模与预测：支持范围、选择方法和交付

先确认预测对象、目标、每行代表什么、预测时点和特征可用时间。程序可以阻止把目标列或拆分时间列直接用作特征，无法自动判定其他字段是否包含未来信息。不要将预测关系解释为因果关系。

## 支持的工作

- 表格回归：连续数值目标；Ridge 线性模型与随机森林候选。
- 二分类/多分类：LogisticRegression 与随机森林候选；给出类别预测、概率、混淆矩阵和分类指标。
- 时间相关表格任务：指定 `--time-column`，最晚的一段不同时间点用于最终留出评估；同一时间戳的记录始终属于同一分区。
- 单一规则时间序列：`--task forecast` 用历史目标的滞后值建立回归候选；支持历史滚动单步评估和显式标注的未来递归多步预测。

这不是自动因果推断、深度学习、面板预测、模型上线或无限搜索 AutoML。当前没有实体分组拆分接口；同一客户/设备/病人的重复观测可能跨随机分区，必须先判断是否需要分组验证。仅指定时间列不能消除主体重复或标签观察窗口重叠。

## 运行示例

以下命令在包含 `datalab` 的脚本目录或已安装的 DataLab 环境执行。

```text
python -m datalab model --input sales.csv --out results/regression --target revenue --features visits price channel --task regression
python -m datalab model --input customers.csv --out results/classification --target churn --features tenure spend region --task classification
python -m datalab model --input observations.csv --out results/time-evaluation --target outcome --features feature_a feature_b --task regression --time-column date
python -m datalab model --input monthly.csv --out results/forecast --target revenue --task forecast --time-column month --forecast-lags 1 2 3 12 --forecast-horizon 6
```

`--sheet` 可指定 Excel 工作表名称或从 0 开始的索引。目录必须为空，避免覆盖旧成果。默认比较 `linear` 和 `random_forest`；可用 `--model-families linear` 只运行线性候选。默认留出比例为 20%，训练交叉验证最多 5 折，随机种子为 42。

`auto` 将文字/布尔目标或只有两个不同值的数值目标解释为分类。只有 3–20 个不同整数值的数值目标有分类/回归歧义，会要求明确任务；其他数值目标解释为回归。数值编码的多分类目标应明确写 `--task classification`；只有两个取值的连续指标应明确写 `--task regression`。正式分析建议始终明确任务类型。

不指定 features 时会按字段名排除 `id`、`*_id`、UUID、编号、电话等疑似标识符，并记录排除列表；该规则不是完整泄漏检测。若确有预测含义，可通过 features 显式启用。不同带时区偏移的时间会统一到 UTC；混合带时区与无时区时间会被拒绝。全为无时区的时间保持原钟面时间并标记 UTC，此假设会出现在报告，跨来源比较前需明确实际时区。

## 怎样避免验证污染

1. 先留出测试分区，然后只在训练分区内部比较候选及有限的参数取值。
2. 数值特征的中位数填补和标准化、类别编码都在 sklearn `Pipeline` 内。每个验证折重新拟合，最后的评估模型也只在训练分区拟合。
3. 默认回归按训练 CV 的 RMSE 选择，分类按训练 CV 的 macro F1 选择；简单基线也参与比较，允许得出“复杂模型没有增益”的结果。
4. 留出集只在候选确定后评估选中模型和基线。不能依据这次留出结果继续调参，再把同一留出集称为独立测试。
5. 分类随机拆分使用分层，CV 折数依据训练集最小类别样本数收缩。不可行时明确报错，不偷偷换为无分层拆分。
6. 时间拆分和扩展窗口 CV 均按不同时间戳分组，保证训练末时点早于验证/测试首时点。日期不规则时仍可评估普通表格任务，但各折并非等时间跨度，报告会说明。

缺失目标在普通分类/回归中会被计数并排除；非数值或无穷回归目标直接报错。特征缺失在训练内处理，特征无穷值按缺失处理并记录数量。近似 ID 的高基数类别会被提示；类别编码上限为每列 50 类，未知类别不报错，但可能降低预测能力。

## 预测模式的准确含义

`forecast` 限于一个主体、一行一个时间点、规则且无缺期的数值序列。缺失目标、不规则间隔或重复时间戳会报错，不会静默插值、合并主体或删除缺期。

历史评估是**滚动单步预测**：预测某期时，可以使用上一期已经观测到的真实数值，包括此前留出区间的真实观察。这不等于“从留出期开始前一次性预测整个留出区间”。基线为上一期真实值。滞后变量只由更早的目标创建，最初不足滞后长度的记录会被计数并排除。

完成独立留出评估之后，才将选中候选重新拟合到全部历史数据，递归产生未来多期结果。后续步使用前面预测出的值，因此误差可能累积。**未来多步结果没有预测区间，也没有对应的固定起点多步历史准确率**；不能用单步留出指标替它背书。复杂季节性、外生变量、多主体、较长预测期或生产决策需另建验证设计。

## 结果文件

| 文件 | 内容 |
|---|---|
| `model_report.md` | 验证方法、留出指标、基线、限制与复现说明 |
| `model_metrics.json` | 版本、预处理审计、拆分窗口、各类指标、警告及候选结果 |
| `model_comparison.json` | 每个候选的训练 CV 各折分数、均值、标准差或失败原因 |
| `holdout_predictions.csv` | 留出行的原始行位置、真实值、预测、基线和分类概率 |
| `split_assignments.csv` | 有效建模行对应的训练/留出分区及时间 |
| `model_diagnostics.png` | 回归实际与预测/残差或时间图；分类混淆矩阵 |
| `future_forecast.csv` | 仅预测模式：未来时间、递归预测及持续性基线 |
| `model_config.json` | 输入数据 SHA-256、参数和原始文件位置 |
| `code/`、`reproduce.py` | 运行时 Python 源码与复现入口 |

不自动复制原始数据，不保存或加载 pickle/joblib 模型。换机器复现时，在独立环境安装报告列出的依赖版本，然后提供原始数据路径与新输出目录：

```text
python reproduce.py --input original.csv --out reproduced
```

复现需要兼容 Python 和已安装依赖，不代表运行环境、凭证或文件已经跨设备同步。随机森林不纯度重要性可能偏向高基数特征；线性系数来自标准化/编码后的特征空间；二者均不能当成因果效应。

## 官方 API 依据

- [sklearn Pipeline 与组合转换](https://scikit-learn.org/stable/modules/compose.html)
- [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [SimpleImputer](https://scikit-learn.org/stable/modules/generated/sklearn.impute.SimpleImputer.html)
- [OneHotEncoder](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.OneHotEncoder.html)

代码只使用 Python 3.11+ 与常见 sklearn 1.3+ API；具体已验证版本以项目验证记录和运行输出为准。只完成 macOS 实跑时不能宣称已完成 Windows 实机测试。
