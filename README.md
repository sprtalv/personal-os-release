# Personal OS

An experimental Skill for organizing selected personal evidence, plans, corrections, decisions, and follow-up outcomes. This repository contains generic workflow guidance, Python tools, templates, and synthetic tests. It contains no personal archive.

## Install

1. Make .agents/skills/personal-os/SKILL.md accessible to an assistant host that supports local Skills. Host support and automatic triggering must be verified separately.
2. Use Python 3.10+ on Linux. Create an isolated environment and install .agents/skills/personal-os/scripts/requirements.txt.
3. Create a separate private data directory. Copy .agents/skills/personal-os/assets/settings.example.json to its settings.json; fill only verified values. Keep data and credentials outside this public repository.
4. Inspect commands with python .agents/skills/personal-os/scripts/records.py --help and python .agents/skills/personal-os/scripts/followups.py --help. Run tests with python -B -m unittest discover -s tests -q.

records.py validates and saves structured Markdown records with provenance and cross-references. followups.py tracks due questions and outcomes. publish.py optionally publishes selected files from a separately configured private Git data root. Read the Skill and its references before writes.

Important, reusable understandings and AI suggestions now have separate [insight](.agents/skills/personal-os/assets/templates/insight.md) and [recommendation](.agents/skills/personal-os/assets/templates/recommendation.md) templates. `records.py impact --root ROOT --record-id ID` lists linked files and exact-ID mentions to review after a correction; it cannot repair meaning automatically. [Design sources and limits](.agents/skills/personal-os/references/design-evidence.md) explain the choices without claiming proven long-term benefit.

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

## 通过与 GPT / Codex 问答完成个性化配置

把下面的提示词交给 GPT、Codex 或其他能对话的助手即可开始。它先了解你的选择，再生成可审阅的配置；不要求一次填完问卷。配置的是你自己的**私有工作副本**，上面的 [dot 接入规则](#与-dot-配合使用) 则用于之后的日常使用。

```text
请陪我用 Personal OS 做个性化配置。先读取当前项目的 README.md、.agents/skills/personal-os/SKILL.md、.agents/skills/personal-os/assets/settings.example.json 及本次相关引用，核对真实字段和能力。若你无法读取仓库或没有文件工具，请直说，只做访谈和配置草案，不声称已经安装或写入。

每轮只问 1–3 个最有帮助的问题，依据我已回答的内容调整下一轮；允许跳过，别让我一次填表或反复确认同一授权。分阶段了解：我希望它帮助什么、怎样算有用；当前目标、约束与优先级；值得记录的范围、隐私边界及明确不记的内容；我喜欢的沟通方式，以及你应怎样质疑我的假设；建议、我的决定和真实结果如何区分，何时回填。再询问我的时区、是否需要早晨/晚间/每周/每月功能与各自时间；模型成本预算、可接受的高成本工作；私人数据存放位置、私有远端与本地备份；已连接工具和外部改动的授权边界。非必填的敏感经历由我主动决定是否谈；不要索要密码、令牌、身份证号或完整私人聊天记录。

访谈结束先给我四栏简短结果：已确认的长期偏好、待确认项、建议配置、功能状态（已可用/待接入/待确认）。把当日安排与长期偏好分开，把我的决定与 AI 建议分开。只在我确认这份结果后，才按项目真实模板把偏好写入我自己的私有 MEMORY.md（如采用该文件），把受支持的字段写入我私有数据根目录的 settings.json；未确认的值保持 null，不发明配置字段，也不把个人信息提交到这个公共模板仓库。没有写入权限时交付可复制的草案和移交说明，并明确尚未保存。

技术接入另做验收：检查实际检出的仓库版本、Skill 文件和脚本、私有数据根；用隔离的合成资料试运行。实际验证宿主是否加载 Skill、跨任务是否能访问相同资源、定时任务 ID、消息发送回执及远端读回。未连接的工具标为未启用；提示词或 settings.json 的文字不能代替这些回执。分别报告 GitHub、云工作副本和本地备份状态，电脑离线时标待备份。若宿主不能强制指定模型或推理档位，如实说明，把成本分级当作建议而非已生效的路由。最后交付“已可用/待接入/待确认”清单。
```

实际 [设置模板](.agents/skills/personal-os/assets/settings.example.json) 只有 `timezone`、`morning_time`、`evening_time`、`weekly_review`、`followup_default_days` 和 `repository.remote/branch` 等字段；示例值需要按使用者实际情况核对。现有 `followups.py` 仅接受 7 天默认间隔，其他间隔须标为待接入。更细的记录偏好和外部授权可写入其私有项目说明，平台调度与模型选择须按宿主实际能力配置。当前脚本的部分时间行为有预设时区；目标时区与实际行为不一致时，应列为“待接入”，不要只改配置就宣称已经生效。

合成对话示例（仅展示问法，不是用户资料）：

1. **用户：**“我想整理读书和每周计划。” **助手：**“你希望先解决计划遗漏，还是更好地复盘选择？有哪些内容你明确不想记录？”
2. **用户：**“主要是复盘选择，不要每天催我；也别记健康细节。” **助手：**“明白，是回顾选择，不是每日提醒。你希望有结果时再回填，还是默认每周检查一次？时区与具体回顾时间现在要定吗？”
3. **用户：**“每周检查可以，时间先跳过；只放私人目录。” **助手：**“我先列出确认项与待定项：每周结果检查是你的选择，回顾时间和目录位置待定；我建议先保留对应配置值为 null。这份摘要符合你的意思吗？”
4. **用户：**“符合。” **助手：**“我没有这个目录的写入工具，下面给出私有 MEMORY.md 摘要草案和仅含模板支持字段的 settings.json 草案；状态是‘待写入’，不会称作已启用。”

## Limits

Scripts enforce structural rules, not semantic accuracy. Search is lexical; interpretation, contradiction review, and advice evaluation need source review. No assistant integration, continuous chat capture, scheduler, message delivery, external task/calendar connection, or backup is installed here. Synthetic tests do not establish long-term effectiveness or better personal advice.

## License

No license has been granted for this repository. MIT is a reasonable permissive option after the copyright holder confirms intended terms and attribution. Public availability alone does not grant reuse rights.
