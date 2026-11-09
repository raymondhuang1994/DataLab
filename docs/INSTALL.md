# DataLab 安装与兼容说明

支持目标：Windows / macOS 的具备插件或本地技能能力的 ChatGPT、Codex。一个插件包可以共用工作流，但账号、数据连接、执行环境和文件权限需要分别配置。安装 Skill 文本不会自动安装 Python 或连接数据库。

## 选择安装方式

从 [GitHub 仓库](https://github.com/raymondhuang1994/DataLab) 获取源码，或从 [Releases](https://github.com/raymondhuang1994/DataLab/releases) 下载完整包/独立 Skill 包。也可执行 `git clone https://github.com/raymondhuang1994/DataLab.git`，然后进入 `DataLab` 目录。

1. **插件方式**：使用客户端当前提供的本地插件/仓库插件安装入口，选择 DataLab 根目录或发行包。包内同时提供标准 `plugin.json` 和 `.codex-plugin/plugin.json` 兼容清单。若当前客户端仅允许目录中的已发布插件，需要先走对应发布/审核流程；GitHub 公开源码不等于已上架客户端插件目录。
2. **本地技能方式**：将整个 `skills/data-lab` 目录复制到 Codex 当前使用的个人技能目录或项目技能目录；可使用下面的显式安装脚本。不要只复制 `SKILL.md`。
3. **先试用执行工具**：不安装插件也可在项目中运行 Python CLI，验证数据分析、建模、看板和 Word 导出。

不同 Codex 版本使用的技能目录可能不同；选择当前客户端明确显示的目录。仓库提供的 `tools/install_skill.py --dest <技能父目录>` 不会自动猜测或覆盖现有安装。

## Windows（PowerShell）

先安装/选择 Python 3.11+，推荐 3.12。进入 DataLab 根目录，执行：

```powershell
py -3.12 skills/data-lab/scripts/bootstrap.py --venv .venv --parquet --interactive
& .\.venv\Scripts\python.exe skills/data-lab/scripts/run.py doctor
& .\.venv\Scripts\python.exe skills/data-lab/scripts/run.py analyze --input examples/sales.csv --metric revenue --group region --date date --agg sum --out demo-output
& .\.venv\Scripts\python.exe skills/data-lab/scripts/run.py word --bundle demo-output/result.json --out demo-output/report.docx
```

若 Python 安装版本不同，将 `py -3.12` 替换成已有的合适解释器。无需激活 PowerShell 脚本或修改 ExecutionPolicy。

## macOS（Terminal）

系统 `/usr/bin/python3` 可能太旧；选择 Python 3.11+。以下以 3.12 为例：

```bash
python3.12 skills/data-lab/scripts/bootstrap.py --venv .venv --parquet --interactive
.venv/bin/python skills/data-lab/scripts/run.py doctor
.venv/bin/python skills/data-lab/scripts/run.py analyze --input examples/sales.csv --metric revenue --group region --date date --agg sum --out demo-output
.venv/bin/python skills/data-lab/scripts/run.py word --bundle demo-output/result.json --out demo-output/report.docx
```

ChatGPT/Codex 若已有合适的代码执行环境，可复用该环境，不必再创建一份。Windows 和 macOS 路径含中文/空格时，把整个路径放在引号中。

## 实时看板

```text
python skills/data-lab/scripts/run.py serve --input examples/sales.csv --date date --metric revenue --group region --agg sum --interval 5 --port 8765
```

使用实际环境的 Python 路径替换 `python`，在同一台机器浏览器打开终端显示的地址。更改并保存 CSV 后会刷新。Ctrl+C 停止。关闭进程后不再刷新，不安装系统后台服务。

这是**实时读取本地文件变化**的实现；数据库/业务系统更新必须通过现有授权连接或单独的数据采集流程写入该文件。默认不向局域网公开。浏览器端 ChatGPT 不会自动访问你电脑的 localhost。

## Word 和字体

生成 `.docx` 不需要安装 Word。实际分页应在目标 Word/LibreOffice 或宿主预览器检查。中文优先使用已安装的 Microsoft YaHei/PingFang/Noto Sans CJK；容器可能需要另外可用的中文字体。没有随包分发系统专有字体。

可使用 `skills/data-lab/scripts/render_word.py` 配合 LibreOffice 生成 PDF/页图进行检查，详见技能 `references/word.md`。导出成功不等于版式已经人工检查。

## 当前验证范围

本项目在 macOS arm64 / Python 3.12 的独立环境中开发验证。提供 Windows/macOS CI 测试配置，但未获得 Windows 实机执行结果。客户端安装、账户权限和云端运行能力应在实际目标环境完成验收，不能根据文件结构直接宣称全部兼容。

官方参考：[插件打包](https://developers.openai.com/plugins/build/plugins)、[Claude 插件迁移](https://developers.openai.com/plugins/guides/submit-claude-plugin)。

## v0.2 项目与偏好

配置使用方式与旧脚本迁移见 [v0.2 使用说明](UPGRADE-0.2.md)。实际字段合同见技能 `references/settings.md`。复制整个技能目录时会保留执行工具、指标计算模块及离线看板JavaScript资源，不要仅复制SKILL.md。
