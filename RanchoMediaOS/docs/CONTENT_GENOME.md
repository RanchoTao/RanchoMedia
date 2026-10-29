# Content Genome 与创作证据

schema_version=1，不可变；canonical 与每个平台 revision 均有 Genome。它解释“发了什么、基于什么、采用哪版策略”，不存分析结果。planned_publish_at 是计划；actual publication time 归 publications，以保留审批时的 Genome。

| 字段 | 语义 |
| --- | --- |
| topic / subtopic | 可配置领域标签与更细主题 |
| angle / claim / hook | 独立观点、待验证主张、开头机制；claim 映射 claim_evidence |
| format / platform | article/thread/note/script/video 等；canonical 或具体 platform |
| tone / length | 枚举风格与实际字符/字数/视频时长单位 |
| technical_depth / novelty | 0..1 rubric 分数，记录 scorer/version；不是实验测量或创新事实 |
| visual_style / cta | 视觉机制与行动邀请 |
| trend_source / references | source/trend IDs、URL/hash/retrieved_at/证据可信度；不得虚构引用 |
| publish_time | 接口概念映射 planned_publish_at；实际时间单独关联 publication |
| model / prompt_version / strategy_version | 实际生成来源；fixture 使用 mock 标识，不能伪称 OpenAI |
| provenance_kind / model_call_id | MODEL / FIXTURE / HUMAN；追溯生成运行 |

StyleSignature 保存 hook_type/title_pattern/structure/tone/sentence_length/technical_depth/image_count/cta_type；例如“反直觉开头 → 个人证据 → 方法 → 讨论”，不保存能直接换词发出的热门原文模板。来源原文与 signature 分开存，署名/许可/适当引用仍需检查。

相似检查是风险门，不是原创证明：规范化字符 n-gram overlap + exact substrings；semantic embedding compare 候选参考与自有历史，embedding model/version 和语种阈值要标定。过相似为 BLOCK；语义检查缺失/失败为 UNKNOWN，真实发布不得 PASS。技术常用词与短文本需专门 rubric；授权引文也需标注比例/引用关系，不能靠删除 references 降相似度。模型不为自己判定“可以抄”。

Canonical draft 可产生 X thread、小红书 note、Bilibili script、website article；每份变体有独立长度、标题、CTA、媒体权利和 fact/risk/similarity 复检。script 不是可发布视频，缺少 media_asset 则 BLOCK；不能把一份文章复制多平台当原生 adaptation。

输入来源与评论视为不可信数据，引用内容不能改写 system policy、执行工具或向外发送 credentials。FactCheck 逐 claim 记录 SUPPORTED/CONTRADICTED/UNKNOWN；没有证据时保留未知或移除主张，不编造实验、引用、指标和“个人经历”。
