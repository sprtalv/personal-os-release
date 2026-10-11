# 记录与档案契约（版本 1）

## 内容
- 选择记录与时间
- 统一 Markdown 格式
- 基础记录与假设
- 案例、概况与报告
- 更新、纠错、检索

## 选择记录与时间

八个领域可供用户按需授权：基本背景与约束、学业科研与项目、目标偏好习惯兴趣、观点价值观、重要经历与决策、情绪精力健康、关系互动、财务消费。实际记录范围以用户在其私有工作副本中确认的选择为准；明确不记的内容不保存。每轮检查，仅保留授权范围内影响安排、理解自己或形成经验的内容。

保留否定、条件、范围、主观语气和临时性；先写明确内容，把关键歧义单独列出。读原话及必要上下文，再检索旧记录，选择新增、补充、跳过重复、纠错或记录变化。不要因两个日期的经历措辞相近就合并，不把原记录在概况/报告中的多次引用算成多份证据。

采用 Asia/Shanghai。`recorded_at` 和 `updated_at` 由保存脚本读取真实时钟，包含时区；更新保留原 `recorded_at`。发生、表达、计划针对的时间按需填写；原消息的“昨天”按原消息时间解析。导入时间不能代替原表达时间。模糊范围在 `time.note` 保留原说法，无法解析的值用 null，不虚构时刻。

## 统一 Markdown 格式

用 YAML frontmatter 保存机器字段，正文保存简要内容、关键原话与信息边界。日期时间字段加引号，避免 YAML 隐式转换。使用 [模板](../assets/templates/record.md) 生成候选内容，然后由 [records.py](../scripts/records.py) 渲染和检查。

共用字段：`schema_version: 1`、`id`、`document_type`、`recorded_at`、`updated_at`。ID 用小写字母/数字/连字符，最长 96 字符，跨目录唯一。ID 表示一条逻辑记录；修订保留 ID，新经历使用新 ID。所有关联使用真实已存在或同一写入包中创建的 ID。

路径和类型对应：

| 路径 | document_type |
|---|---|
| `data/records/YYYY-MM/<id>.md` | record |
| `data/cases/<id>.md` | case |
| `data/followups/<id>.md` | followup |
| `data/current/<id>.md` | summary |
| `reports/<id>.md` | report |

`operations/` 是运行回执，`generated/index/` 是可重建索引，都不作为另一套个人事实库。

## 基础记录与假设

record 必填字段：

| 字段 | 值与意义 |
|---|---|
| kind | experience / observation / feeling / view / intention / decision / hypothesis / outcome / correction / insight / recommendation |
| author | user / ai / external；表示原内容或判断的作者，不是文件写入者 |
| basis | user_statement / tool_observation / ai_inference / imported_source |
| domains | 非空列表，采用下方领域标识 |
| status | active / corrected / superseded / disputed |
| source | 对象，包含 ref、message_at、excerpt；引用确实不可获取时 ref 为 null |
| time | 对象，包含 event、expressed、target、precision、note |
| related | 关联 ID 列表 |

领域标识：`background`、`study_research`、`goals_preferences`、`views_values`、`experiences_decisions`、`wellbeing_health`、`relationships`、`finance`。

`source.message_at` 是带时区的原消息时间或 null。`source.ref` 只填真正可用的标识/链接，不捏造消息 ID。缺少引用时必须保留非空 `excerpt`，正文写明例如“来源不可用：仅保留用户提供的原话。”来源可访问时也为重要判断保留必要短句，避免仅依赖可能失效的链接。

`time.event`、`time.expressed`、`time.target` 为 ISO 日期、月份、带时区时间或 null；precision 为 instant / day / month / range / unknown。混合精度或范围在 note 中逐项说明。感受与观点记录表达时期；“想明天尝试”中的明天放在 target，不能当作今天表达意向的时间。

`active` 表示可用于检索，不代表已证实。`author:user` + `kind:hypothesis` 表示你的判断，不能用“本人陈述”把判断改写成事实。

hypothesis 额外要求：
- `user_endorsement`: pending / accepted / rejected。
- `evidence_state`: untested / supported / mixed / contradicted / inconclusive。

本人假设与 AI 假设分别建档，保留初始来源。用户认可 AI 解释后，作者仍是 ai。只有经过讨论接受的假设可进入当前概况，且正文必须明确标注“假设/待验证”、依据与限制。用户认可不证明因果；根据支持材料、相反证据与替代解释更新 evidence_state。

重要认识使用 [insight 模板](../assets/templates/insight.md)：`context` 描述适用情境，`valid_from/valid_until` 限定适用时期（未知为 null），`evidence_refs` 至少一条，`counterexample_refs`、`unknowns` 与 `revises` 保留反例、缺口和旧版关系。`user_endorsement` 与 `evidence_state` 分别表达本人是否认可、证据是否支持；认可不等于证实。新认识修订旧 insight/hypothesis 时，旧项须同时标 corrected 或 superseded。来源、适用范围与推断要在正文中可读，字段通过结构校验仍需人工核对语义。

`evidence_refs` 和 `counterexample_refs` 只指向基础经历、观察、感受、观点、意向、决定、结果或纠错记录，不拿 AI 建议或另一条认识互相引用来冒充独立证据。

重要 AI 建议使用 [recommendation 模板](../assets/templates/recommendation.md)：`context`、`evidence_refs`、`counterexample_refs`、`unknowns` 与原始 `expected_result` 记录当时依据、条件、限制和可观察预期；`adoption_state` 为 unknown/adopted/declined，未知不是拒绝或失败。采用或拒绝必须有 `adoption_ref` 指向用户自己的 decision/outcome；`process_refs`、`outcome_refs`、`side_effect_refs` 记录执行、结果和副作用，`revision_refs` 指向新的 insight，`revises` 指向已失效的旧建议。原始情境、依据与预期在同一建议中不可改写；新预期创建新版建议。状态变化仍须检查原消息，不能由工具自动判断用户意愿或建议成效。

## 案例、概况与报告

case：`kind` 为 decision / state_change / improvement；`status` 为 open / observing / closed / paused。保留 `record_refs`、`user_hypothesis_refs`、`ai_hypothesis_refs`、`outcome_refs` 四个列表；新案例可另列 `insight_refs`、`recommendation_refs`。结果先写成 outcome 基础记录，再加入案例的 outcome_refs。本人/AI 假设列表分别只引用对应作者的 hypothesis。重要项目可选填 `purpose`、`investment_boundary`、`pause_condition`，记录为什么做、愿意投入多少和何时暂停；旧案例不强制补填。

正文按 [案例模板](../assets/templates/case.md) 组织：当时情况与目标、当时信息与约束、选择理由、预期、实际过程、阶段及长期结果、支持与反对解释、下一步。只填真实知道的内容，事后回忆标注补充时间与不确定性，不伪装为事前信息。观察期限/下一次检查时间以 followup 为准，不在案例维护另一份可编辑日程。

summary：`kind: current`，以 `record_refs` 指向有效依据。概况不得依赖 corrected/superseded 记录；假设和 insight 不得以 contradicted 状态晋升，必须 user_endorsement=accepted 且正文显著保留暂定身份。AI recommendation 不能作为当前个人事实的依据。disputed 信息如需提及应标明争议，不能写成定论。

report：`kind: periodic`，保存 `record_refs`；正文标明覆盖时期、资料范围、旧认识、变化、结果与缺口。历史报告可以引用当时观点，但更新说明应指出已知纠错，避免旧错误继续成为建议依据。

current 和报告生成前检查来源及状态。它们是可追溯的整理结果，不能作为绕过原始依据的第二套事实来源。

## 更新、纠错、检索

记录变化：新增观点记录，关联前项；旧项可置 superseded，保留当时有效的时间与原因。纠错：旧项标 corrected，新增 correction 说明正确内容与依据；同一变更包修正依赖旧错误的当前概况，并检查案例与报告。来源冲突未解决时用 disputed，不自动相信较新的说法。

`records.py impact --root ROOT --record-id ID` 列出结构化反向引用和正文中的精确 ID 命中。纠错/废止的保存回执也会给出 `review_required` 清单，供执行者逐项检查案例、报告、认识及建议；当前概况若仍引用失效或被反证的记录则拒绝保存。此清单不会自动发现改写后的措辞、外部发布内容或语义上受影响但未引用 ID 的叙述，仍须人工检索和判断。

更新已存在文件必须提供旧文件内容的 SHA-256，先检查其他任务是否修改。全局引用检查在整个候选变更包上运行，因此可同时创建相互关联的记录和案例。结构校验失败先修复，不跳过。

日常检索默认排除 corrected/superseded；历史问题显式启用历史检索并标注其时期和状态。检索步骤见 [retrieval.md](retrieval.md)。
