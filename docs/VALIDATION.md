# DataLab 0.3.0 验证记录

日期：2026-11-09。本轮重点为任务原件与历史文件保留；保护范围是已接入的内置写入，不能替代宿主对任意代码的只读权限。

| 检查 | 实际结果 |
|---|---|
| 本地完整回归 | macOS arm64 / Python3.12：281 passed、2 skipped，25.58秒；两项跳过为Windows专属测试 |
| 原生跨平台CI | Windows/macOS × Python3.11/3.12/3.13共6组全部通过；macOS每组281 passed、2 skipped，Windows每组278 passed、5 skipped；跳过项为另一平台专属测试 |
| 业务场景计算与验证 | 28/28通过，原有指标计算口径保持一致 |
| 覆盖保护 | 已有原件、配置、报告、发布包、安装与环境目标拒绝覆盖；已有空分析目录也拒绝 |
| 运行所有权 | 同次命令可更新自己新建且未被外部修改的产物；后续命令不继承权限 |
| 路径和并发 | 独占创建；硬链接、目录穿越、父目录替换、mkdir竞争与根目录替换回归通过；没有向原始目录新增文件 |
| 来源核对 | 运行前后SHA-256/身份/大小/权限位/修改时间核对；外部变动记录失败，不恢复或写回原件 |
| 输出核对 | 审计核对登记产物的最终身份和摘要；审计自身不含在自引用摘要清单中 |
| Word转换 | 原DOCX保持不变；LibreOffice仅接收任务内副本，转换为PDF成功；本轮未重复宣称完整Office视觉验收 |
| 维护命令 | bootstrap拒绝已有环境；安装、打包、合成样例生成使用新目标 |
| Skill结构 | 元数据、相对链接、Python语法和打包资源检查通过 |
| 解压包试用 | 源码包与独立Skill包均完成分析、验证、Word和看板；重复输出被拒绝，原始样例、包内文件与已有成果保持不变 |

## 独立使用任务

新代理仅获得Skill、原始业务请求和合成输入，完成渠道转化分析、图表、HTML、Word及复现代码。父代理另核对原始CSV、notes及历史Word，三者字节指纹均未变化。

7行输入仅删除1行完全重复记录，6行整理结果另存。valid为5行，其中4行具有完整分子/分母：整体28/350=8%、Search18/150=12%、Social10/200=5%。缺失访问量的记录保留并披露，未将完整配对样本的率冒充全部valid的准确率。计算检查通过；Word预览有中文缺字，明确标为版式未完成目视验收。该任务验证有限场景下的行为，不代表任意模型或任意额外代码都会遵守规则。

## 保护边界

- `software_guards`明确标记`os_read_only_enforced: false`；没有安装或配置全进程操作系统沙箱。
- POSIX使用目录描述符相对写入；Windows实现目录句柄锁定。真实Windows执行结果以远程CI记录为准。
- 任意额外Python/SQL、第三方库及外部程序仍需宿主只读输入权限。外部写入器记录预期位置，不声称完整捕获其全部写入。
- `serve`允许上游更新源文件，按每次读取检查稳定性，不把上游合法更新当成DataLab写回。
- 客户端安装、用户电脑权限配置、Windows Microsoft Word排版和大规模压力测试不在本轮自动验证范围内。

v0.2历史记录保留在[VALIDATION-0.2.md](VALIDATION-0.2.md)。文件使用规则见[READ-ONLY.md](READ-ONLY.md)。

远程CI：[本版6组完整检查](https://github.com/raymondhuang1994/DataLab/actions/runs/42548889068)，运行代码提交`de8c18563d95e538b16e392b13a5ac6d0bdc3fba`。每组均完成包结构、pytest、doctor、28项业务场景和打包。首次运行定位并修正了Windows元数据目录句柄未参与共享检查的问题；保留原回归断言后重新通过。发布前的后续变更仅补充文档及验证证据。

可机读记录：[跨平台CI](../evaluations/observed/cross-platform-ci-0.3.json)、[业务评测](../evaluations/observed/business-benchmarks-0.3.json)、[独立任务](../evaluations/observed/readonly-agent-task-0.3.json)、[候选包试用](../evaluations/observed/package-smoke-0.3.json)。候选包试用在本地macOS执行；其后Windows句柄修复由上述原生CI覆盖，最终发布包另核对与Git提交的文件字节一致。
