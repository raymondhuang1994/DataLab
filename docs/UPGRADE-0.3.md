# DataLab v0.3 使用与升级

本版把“原始数据不改动”扩展为明确的文件规则：已有文件只读，成果使用新路径，内置写入走统一保护入口，并记录输入前后指纹与新产物。分析、指标、建模和报告范围继续沿用 v0.2；没有新增 PPT 或跨设备同步。

## 直接对助手说

> 使用 DataLab 分析 inputs/销售.xlsx，按 config/project.json 的指标计算。所有原件、配置和旧成果保持不变。把图表、HTML 和 Word 写到新的 results/分析-001，报告输入指纹、检查结果及保护边界。

> 读取上一版成果，重新审查结论。需要修订时在新任务目录建立完整副本，保留原版；交付新版 Word 和新验证记录，不覆盖已有文件。

## 示例

按 [安装说明](INSTALL.md) 选择现有合适的 Python 环境或创建新环境。从 DataLab 根目录运行；下列成果路径必须尚不存在：

```text
python skills/data-lab/scripts/run.py analyze --input examples/sales.csv --project examples/project.json --preferences examples/preferences.json --metric-id gross_margin --out ../datalab-results/gross-margin-001
python skills/data-lab/scripts/run.py word --bundle ../datalab-results/gross-margin-001/result.json --out ../datalab-results/gross-margin-001/report.docx
python skills/data-lab/scripts/run.py validate --bundle ../datalab-results/gross-margin-001/result.json --out ../datalab-results/gross-margin-001/checks-review-001.json
python skills/data-lab/scripts/run.py report --bundle ../datalab-results/gross-margin-001/result.json --out ../datalab-results/gross-margin-001/report-v2.html
python skills/data-lab/scripts/render_word.py --input ../datalab-results/gross-margin-001/report.docx --out ../datalab-previews/word-001
```

`analyze` 已生成 `report.html` 和 `checks.json`；后两条命令只是演示如何在确需重导出或复核时使用新文件名，不要求每次分析后重复运行。原始 `examples/` 目录不会作为输出位置。Word 预览放在 DOCX 父目录之外。

## 旧调用如何迁移

| v0.2 常见写法 | v0.3 用法 |
|---|---|
| 预先创建空目录后传给 analyze/model | 让命令创建尚不存在的新目录 |
| 重复使用同一 `--out` | 新任务用新目录，重导出用新文件名 |
| 在原始数据旁保存新报告 | 成果放到原始数据父目录之外 |
| 修改已有 project/preferences/context | 保存新版本，显式传入新路径 |
| 在已有 venv 上再次运行 bootstrap | 直接使用依赖齐全的环境；缺依赖时创建新环境 |
| package 指向之前的发布目录 | 每次打包指定全新目录 |

完整复制/解压新版到独立位置，保留旧安装。安装脚本拒绝覆盖现有 `data-lab`；切换当前客户端使用的 Skill 路径应通过宿主支持的安装方式完成，不自动删除或替换旧安装。

## 如何读保护结果

输出根目录中的 `audit/file-safety-*.json` 是文件保护记录；分析的 `checks.json` 是数据/计算验证记录，二者职责不同。`software_guards` 表示内置工具执行了软件层检查，`os_read_only_enforced: false` 表示该记录不证明宿主提供了操作系统隔离。`status: completed` 也不等于报告可以直接对外。

额外脚本和外部程序必须遵守同一任务规则，但不会自动被内置写入函数限制。要限制所有代码写原件，需要宿主实际提供只读输入权限。源文件哈希只能检测变化，不能阻止变化或归因。详见 [文件只读与新成果写入](READ-ONLY.md)。

支持平台和实际执行验证仍以 [验证记录](VALIDATION.md) 为准；文档中的保护设计不等于已经完成某台 Windows/macOS 电脑的权限隔离验收。
