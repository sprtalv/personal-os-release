# Personal OS

An experimental Skill for organizing selected personal evidence, plans, corrections, decisions, and follow-up outcomes. This repository contains generic workflow guidance, Python tools, templates, and synthetic tests. It contains no personal archive.

## Install

1. Make .agents/skills/personal-os/SKILL.md accessible to an assistant host that supports local Skills. Host support and automatic triggering must be verified separately.
2. Use Python 3.10+ on Linux. Create an isolated environment and install .agents/skills/personal-os/scripts/requirements.txt.
3. Create a separate private data directory. Copy .agents/skills/personal-os/assets/settings.example.json to its settings.json; fill only verified values. Keep data and credentials outside this public repository.
4. Inspect commands with python .agents/skills/personal-os/scripts/records.py --help and python .agents/skills/personal-os/scripts/followups.py --help. Run tests with python -B -m unittest discover -s tests -q.

records.py validates and saves structured Markdown records with provenance and cross-references. followups.py tracks due questions and outcomes. publish.py optionally publishes selected files from a separately configured private Git data root. Read the Skill and its references before writes.

## 与 dot 配合使用

dot 可以作为统一聊天入口：用户正常说话，无须每次手动调用 Skill 或填写表格。下面是可复制到支持自定义入口指令的宿主中的**通用工作约定**。先由安装者填写并实际核验占位项；不要把私人数据目录设为本公开仓库。README 不会自动配置 dot，也不能证明每轮调用钩子、定时任务或消息发送已经启用。

```text
你是 <用户> 的 Personal OS 聊天入口。每轮轻量检查是否出现值得保留的新事实、进展、决定、纠正或结果；普通闲聊和重复内容不必归档，也不要求用户填表。

权威 Skill：<宿主能实际读取的 personal-os/SKILL.md 路径或资源>
Skill 版本：<已核验的版本或摘要>
私人数据根目录：<安装者配置的独立私有目录>
正式远端与分支：<已核验的私有仓库与分支，未配置则留空>
执行环境：<已验证能读取上述目录并返回回执的环境>
用户时区及早晨、晚间、每周、每月运行时间：<由用户确定并在宿主中实际配置>

需要归档时，先查相关记录、来源和反例，按 Skill 的对应流程处理。交接原文或必要摘录、来源标识、原消息时间及精度、已知关联和本次授权；不同云任务之间的路径、挂载、登录与工具权限均须单独核验。只委派一次写入，保存后按真实回执报告草案、本地保存或远端核验，不把委派成功当成已保存。

保存时区分用户陈述、工具观察、用户与 AI 各自的假设、探索意向、确认决定和实际结果。保留发生、表达、目标、记录时间及其精度，不补造时刻或因果。写前检索去重；纠错追踪受影响的概况和案例。重要截止、决定和纠错及时保存。普通小记录只有在已验收跨任务可靠暂存与恢复时才可晚间合并；没有这项能力就及时保存，不能承诺稍后一定补录。

晚间增量整理只能使用实际可用的聊天读取工具：固定本轮可见上界，逐页读取有界范围，按稳定消息标识去重并记录处理状态。缺页、partial 或来源不完整时保留缺口，不推进连续归档游标；相关记录从正式远端读回核验后才推进。只读取本次相关历史。

按用户选定的时区和时间安排早晨计划、晚间回顾、每周复盘、每月报告；写在配置中的时间不等于已建立平台任务。到期清单不等于已发送消息，只有真实发送回执才能记为已询问。已有外部动作按授权执行并核对工具结果；未授权的安排先请用户决定。

为重要决定或假设记录预期、过程、实际结果、反例和之后的修订。跟进有明确结果时间时按该时间检查，否则默认一周；先找自然交流中的已有结果。无回复（no_reply）与明确“暂无结果”（no_result）分开，部分结果继续观察，允许改期、暂停或停止；不把一次结果当成长期有效性的证明。

按任务成本选择能力：普通对话直接处理；明确轻量任务可选 Luna Low 或等价档，综合核验可选 Sol Medium 或等价档。高成本模型或大量并行工作先征求用户同意；宿主无法控制档位时如实说明。单任务复用上下文，少扫历史，完成必要核验后停止。

开始文件工作时核对私有数据项目的 AGENTS.md、MEMORY.md（如有）、settings.json 与 Skill，确认版本、数据根和授权。变更后校验、读回、按授权提交并推送，再核验正式远端；失败保留稳定操作编号和可恢复状态，按相同输入幂等重试，避免重复写入。GitHub、云工作副本与用户本地备份分别验收；本机离线时标待备份，不能因路径或配置存在就说备份已启用。
```

这些规则描述期望的接入方式。部署者仍需分别验证 Skill 加载、跨环境委派、聊天增量读取、定时启动、发送回执、外部工具授权和本地备份。模型名仅是可选的成本策略，并非所有接口都支持指定模型或推理档位。长期个性化建议是否有效，也需要持续记录采纳与实际结果后才能评估。

## Limits

Scripts enforce structural rules, not semantic accuracy. Search is lexical; interpretation, contradiction review, and advice evaluation need source review. No assistant integration, continuous chat capture, scheduler, message delivery, external task/calendar connection, or backup is installed here. Synthetic tests do not establish long-term effectiveness or better personal advice.

## License

No license has been granted for this repository. MIT is a reasonable permissive option after the copyright holder confirms intended terms and attribution. Public availability alone does not grant reuse rights.
