# 对话知识与提示词工作区

这个目录是当前平板语音数字人资料的唯一整理入口，供人工校订。运行时知识检索使用 AiSales 产品资料副本；数字员工策略和可编辑提示词也统一放在同一目录下。

## 目录说明

- `02_aisales_product_knowledge/`：本项目唯一的事实知识源，使用 AiSales 策略绑定的产品资料副本。以后产品 QA、介绍话术、建设方案和政策资料只在这里维护。
- `03_digital_employee_strategy/`：当前策略加载器使用的主提示词、策略组装配置和按问题选择的销售技能。
- `04_editable_prompts/`：把代码中目前硬编码的系统提示词、知识库 Agent 提示词、公司背景、回答边界和语音输出规则整理成便于修改的文件。
- `internal_reference/`：内部参考资料，不参与对客知识检索，例如运营 SOP。

## 当前运行链路

当前服务通过 `.env` 的 `KNOWLEDGE_BASE_DIR` 读取 `02_aisales_product_knowledge/`。AiSales worktree 仅作为策略代码的兼容来源，不再作为第二套事实知识库参与维护；`03_digital_employee_strategy/` 是本地可编辑的策略副本。

运行时会读取：

1. `02_aisales_product_knowledge/` 中的 QA、介绍话术、建设方案和政策文章。
2. `03_digital_employee_strategy/main_prompt.md` 中被策略加载器选中的核心章节。
3. 与当前问题相关的销售技能：产品介绍、产品 QA、案例、资料和转人工规则。
4. `04_editable_prompts/` 中的语音回答约束、产品政策边界和知识库 Agent 规则。

## 文档如何进入回答链路

1. 服务启动时，`KNOWLEDGE_BASE_DIR` 指向 `02_aisales_product_knowledge/`，读取其中的 Markdown、纯文本和无扩展名文件；隐藏文件、索引文件和 README 不会进入检索。
2. 位于 `AI素养评价产品QA`、`教师发展评价系统常见场景QA`、`AI宣传招生就业系统常见QA` 目录的 Q/A 会被拆成独立标准问答，问题、答案和同义问法进入优先检索索引。
3. 用户每次提问时，系统先做单轮检索，再按产品范围选择相关 QA 或文档片段；不会把上一轮对话当成知识依据。
4. 同一轮会读取 `03_digital_employee_strategy/` 的主提示词和相关销售技能，并加入 `04_editable_prompts/` 中的公司背景、回答边界、产品政策和问答 Agent 规则。
5. 知识问答 Agent 只能引用本轮检索结果和策略资料，并校验回答中的证据；资料没有依据时输出需要进一步确认的口径。
6. 固定回复从 `04_editable_prompts/06_deterministic_reply_templates.md` 的 YAML 模板读取，触发条件仍由代码控制。

因此，新增资料时只需要把文件放到 `02_aisales_product_knowledge/` 的合适产品目录；新增规则或话术时修改 `03_digital_employee_strategy/` 或 `04_editable_prompts/`。知识库文件在服务启动时建立索引，修改后重启后端即可；可编辑提示词和固定回复会在每轮读取。

## 编辑约定

- 事实、价格、周期、案例、接口和效果数字必须写出明确依据；没有依据就标记“待确认”。
- QA 尽量一问一答，并把常见变体写在同一条的“同义问法/变体”部分。
- 对外话术和内部流程分开编辑；内部工具、CRM、转人工流程不会直接播报给用户。
- 不要把密钥、数据库连接串或本地绝对路径写入知识库文件。

## 当前不参与对客检索的内容

`internal_reference/AI学生综合素质评价系统运营SOP/` 属于内部运营资料，不参与对客知识检索；如需开放其中内容，应先整理成对外话术或 QA，并放回 `02_aisales_product_knowledge/`。
