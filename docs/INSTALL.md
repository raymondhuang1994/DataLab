# DataLab 安装与兼容说明

支持目标：Windows / macOS 的具备插件或本地技能能力的 ChatGPT、Codex。一个插件包可以共用工作流，但账号、数据连接、执行环境和文件权限需要分别配置。安装 Skill 文本不会自动安装 Python 或连接数据库。

## 选择安装方式

从 [GitHub 仓库](https://github.com/raymondhuang1994/DataLab) 获取源码，或从 [Releases](https://github.com/raymondhuang1994/DataLab/releases) 下载完整包/独立 Skill 包。也可执行 `git clone https://github.com/raymondhuang1994/DataLab.git`，然后进入 `DataLab` 目录。

1. **插件方式**：使用客户端当前提供的本地插件/仓库插件安装入口，选择 DataLab 根目录或发行包。包内同时提供标准 `plugin.json` 和 `.codex-plugin/plugin.json` 兼容清单。若当前客户端仅允许目录中的已发布插件，需要先走对应发布/审核流程；GitHub 公开源码不等于已上架客户端插件目录。
2. **本地技能方式**：将整个 `skills/data-lab` 目录复制到 Codex 当前使用的个人技能目录或项目技能目录；可使用下面的显式安装脚本。不要只复制 `SKILL.md`。
3. **先试用执行工具**：不安装插件也可在项目中运行 Python CLI，验证数据分析、建模、看板和 Word 导出。

不同 Codex 版本使用的技能目录可能不同；选择当前客户端明确显示的目录。仓库提供的 `tools/install_skill.py --dest <技能父目录>` 不会自动猜测或覆盖现有安装。

新版应解压/复制到独立新位置，保留旧安装。若目标下已存在 `data-lab`，安装脚本会拒绝；通过宿主支持的方式选择新版位置，不自动替换或删除旧目录。插件安装和运行时文件保护是两件事：安装技能不会自动配置操作系统只读挂载。

## Windows（PowerShell）

先安装/选择 Python 3.11+，推荐 3.12。进入 DataLab 根目录，执行：

```powershell
py -3.12 skills/data-lab/scripts/bootstrap.py --venv ../datalab-env-03 --parquet --interactive
& ..\datalab-env-03\Scripts\python.exe skills/data-lab/scripts/run.py doctor
& ..\datalab-env-03\Scripts\python.exe skills/data-lab/scripts/run.py analyze --input examples/sales.csv --metric revenue --group region --date date --agg sum --out ../datalab-results/demo-001
& ..\datalab-env-03\Scripts\python.exe skills/data-lab/scripts/run.py word --bundle ../datalab-results/demo-001/result.json --out ../datalab-results/demo-001/report.docx
```

若 Python 安装版本不同，将 `py -3.12` 替换成已有的合适解释器。无需激活 PowerShell 脚本或修改 ExecutionPolicy。

## macOS（Terminal）

系统 `/usr/bin/python3` 可能太旧；选择 Python 3.11+。以下以 3.12 为例：

```bash
python3.12 skills/data-lab/scripts/bootstrap.py --venv ../datalab-env-03 --parquet --interactive
../datalab-env-03/bin/python skills/data-lab/scripts/run.py doctor
../datalab-env-03/bin/python skills/data-lab/scripts/run.py analyze --input examples/sales.csv --metric revenue --group region --date date --agg sum --out ../datalab-results/demo-001
../datalab-env-03/bin/python skills/data-lab/scripts/run.py word --bundle ../datalab-results/demo-001/result.json --out ../datalab-results/demo-001/report.docx
```

ChatGPT/Codex 若已有合适的代码执行环境，可直接复用，不必再创建一份；不要为本任务升级该旧环境。`bootstrap --venv` 的路径必须尚不存在，已有虚拟环境或空目录都会拒绝。缺少依赖时选择新路径，建议放在源码目录之外。上述分析目录也必须尚不存在，再次运行时将 `demo-001` 改为新名称。Windows 和 macOS 路径含中文/空格时，把整个路径放在引号中。

原始数据父目录及其子目录不可作为成果位置；示例输入在 `examples/`，成果放在源码目录外的 `../datalab-results/`，便于保持发布源码整洁。报告可追加到既有成果目录的新文件名，不能覆盖原文件。详见 [文件只读与新成果写入](READ-ONLY.md)。

## 实时看板

```text
python skills/data-lab/scripts/run.py serve --input examples/sales.csv --date date --metric revenue --group region --agg sum --interval 5 --port 8765
```

使用实际环境的 Python 路径替换 `python`，在同一台机器浏览器打开终端显示的地址。当上游授权流程更新 CSV 后，看板会读取新版本；服务自身不写回数据。Ctrl+C 停止。关闭进程后不再刷新，不安装系统后台服务。

这是**实时读取本地文件变化**的实现；数据库/业务系统更新必须通过现有授权连接或单独的数据采集流程写入该文件。默认不向局域网公开。浏览器端 ChatGPT 不会自动访问你电脑的 localhost。

## Word 和字体

生成 `.docx` 不需要安装 Word。实际分页应在目标 Word/LibreOffice 或宿主预览器检查。中文优先使用已安装的 Microsoft YaHei/PingFang/Noto Sans CJK；容器可能需要另外可用的中文字体。没有随包分发系统专有字体。

可使用 `skills/data-lab/scripts/render_word.py` 配合 LibreOffice 生成 PDF/页图进行检查，详见技能 `references/word.md`。导出成功不等于版式已经人工检查。

## 当前验证范围

本项目在 macOS arm64 / Python 3.12 的独立环境中开发验证；CI 覆盖 Windows/macOS × Python3.11/3.12/3.13，各版本执行结果见 [验证记录](VALIDATION.md)。Windows个人电脑及Microsoft Word排版、客户端安装、账户权限和云端运行能力仍应在实际目标环境完成验收。内置软件写保护不等于该环境已经配置只读输入权限。

官方参考：[插件打包](https://developers.openai.com/plugins/build/plugins)、[Claude 插件迁移](https://developers.openai.com/plugins/guides/submit-claude-plugin)。

## 设置与升级

配置使用方式与旧脚本迁移见 [v0.2 使用说明](UPGRADE-0.2.md)。实际字段合同见技能 `references/settings.md`。复制整个技能目录时会保留执行工具、指标计算模块及离线看板JavaScript资源，不要仅复制SKILL.md。

v0.3 的新输出路径要求、文件审计与额外脚本边界见 [v0.3 使用与升级](UPGRADE-0.3.md)。旧项目/偏好文件只读；需要修改时保存新版本并显式选择。

`validate` 返回 `checks_status: passed` 和 `status: needs_review` 表示自动计算检查已结束，业务解释或视觉审阅仍需人工判断；这不是命令仍在等待，也不需要反复运行直到状态变为 `ready`。Word 转换与版式检查分别记录，渲染脚本可通过 `--timeout` 限制单个转换进程的等待时间。
