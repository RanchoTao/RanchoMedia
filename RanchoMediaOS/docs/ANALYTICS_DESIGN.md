# Analytics v0.1

Measured truth 来自 adapter/平台或明确 mock simulator；SQL / deterministic functions 派生事实，LLM 只解释。真实数据 simulation=false，mock simulation=true，禁止混合 baseline、reward、strategy。

## Observation contract

发布确认后从 actual published_at 规划 T+10m、1h、6h、24h、72h、7d、30d。Temporal timers 均是相对发布的绝对目标，不能每次从上一次实际采集时间累加以产生漂移。测试用 time-skipping，不能等待30天或把时钟快进伪称实测30天。

每条 snapshot 包含 publication/genome/account/experiment arm、target offset、actual observed_at、source window、平台返回值、collector/adapter/version、raw evidence ref、availability、simulation。延迟 backfill 保留真实 observed_at 与源 window，不能改写成按时抓到。

基础字段：impressions、views、likes、comments、shares、saves、favorites、watch_time_seconds、avg_watch_time_seconds、retention、followers_gained、profile_visits、link_clicks、CTR。每项 nullable；availability=OBSERVED/UNSUPPORTED/PERMISSION_DENIED/DELAYED/ERROR/UNKNOWN。range、scope POST/ACCOUNT、unit、definition_version 同时记录。平台返回真0才存0；频道粉丝变化不强归因到单篇。retention curve 可 JSON series（time offset/unit），平均观看百分比不是 completion_rate。

YouTube Data API 的 video views 与 YouTube Analytics 日窗口不合并伪造精度；X impressions 与 video views 不互换。Postiz 返回 `[]` 视为 UNKNOWN；权限/禁用状态若已知则有独立 health/capability reason，不用空数据计算表现。

## 派生指标

| Metric | Definition / missing rules |
| --- | --- |
| denominator | 视频以 views、text 以 impressions；由 platform/format/version 固定，不因哪个好看临时换 |
| like/comment/share/save_rate | 对应计数 / denominator；计数缺失或 denominator<=0 → null |
| engagement_rate | 指定组成的 likes+comments+shares+saves / denominator；任一要求字段缺失 → null；不将四种事件当唯一人数，率可>1 |
| follow_conversion | attributable post followers_gained / denominator；仅有 account scope 则 null |
| CTR | 同 scope/window 的 link_clicks / eligible impressions；平台原生 CTR 与派生 CTR 分开保存 |
| velocity | 两次同定义累计 snapshot 的 Δcount / Δhours，Δtime>0；不足两次→null；负差记录 correction，不解释成负流量 |
| completion_rate | 平台确认完整观看次数 / 合格 starts；没有计数→null，不能以 avg_watch_time/duration 替代 |
| normalized_performance | cohort-baseline 相对同一种基础指标；生成 baseline/version/sample_n 与 quality 状态 |

## Normalization

cohort = platform + account(account_size bucket 可冷启动补充) + format + age bucket + 同指标定义；baseline 由截止时已观测的历史计算，排除当前评估内容与 future 数据。MVP 默认 sample_n>=20 才发布标准化结果；不足则 INSUFFICIENT_BASELINE。20 是工程门槛配置，不是统计充分性证明。mock fixture 可形成模拟 cohort，仅用于测试。

- relative_lift = actual/expected - 1；ratio=actual/expected 单独存；expected<=0 → null。
- z_score = (actual-mean)/std；std<=0 → null；heavy-tail views 可采用预声明 log1p 版本，不能混标普通 z。
- percentile = (below + 0.5*ties)/n，记录 tie convention 与 cohort；不同样本不作相同置信度承诺。
- account size、历史基线、post age、format 是解释变量。MVP 用透明 strata/baseline，不先训练黑箱 expected_views。跨平台比 normalized effect 而非 raw views，保留 platform 样本量与不确定性。

## Reward 与实验

config version 可采用 views_normalized .15、engagement .20、saves .25、shares .15、follower_gain .20、profile_click .05。这些输入需要**各自相同 cohort 的尺度归一化**，不能把 count、rate、z-score 无说明加权相加。默认 required components；缺失任一 → reward=null + coverage/reason。若选可观测子集，则创建新的 reward_profile/version、明确重配权重，禁止偷偷把缺失变零或自动重归一化后跨平台比较。

manual Experiment/Arm/Publication metadata 第一版支持 same topic/different hook 或 title length，记录 assignment_method MANUAL、topic、platform/account、time/age、media变化与 confounders。非随机样本只能报告关联与探索结果，不能声称因果 lift 或统计赢家；bandit/Thompson/UCB/BO 后续需可用 reward 和探索护栏，no RL。

## Strategy state 与 attribution

Quantitative Strategy State = SQL facts + cohort + query/version/hash + data cutoff + sample counts + uncertainty + cited outcome IDs。LLM Notes = interpretation、hypothesis、next experiment，不可覆盖 facts 或历史版本。每项数值必须能回到 snapshot；proposal promoted 需人审，旧策略可回滚。

内容关联使用 publication → immutable variant → Genome，而不是凭标题拼接。跨平台 canonical attribution 用 content_group_id；网站链接用内容/变体/发布级 UTM，但 click/referrer PII minimization。followers/account change 或 overlap audience 保留 UNKNOWN，不能把平台总增长分摊虚构到每条帖子。

Dashboard：Overview显示总发布数与分平台/定义的views/impressions（不合成一个raw total）、engagement coverage、followers attribution coverage、top topics/contents、platform breakdown；Analytics 可查看每个目标 snapshot、延迟、错误、baseline n。mock indicator 与数据 cutoff 一直可见。cold-start 时 Strategy 推荐收集样本而非宣称知道最佳发帖时间。
