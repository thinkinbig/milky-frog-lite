# Read-Noise Benchmark — Pilot 设计

> 前置文档:`read-noise-problem.md`(问题定义 + Langfuse 实证)。
> 本文是评估的 **设计文档**,把 pilot 的每个决策连同 rationale 固定下来。
> 状态:设计已定,尚未实现。

## 一句话

在 **SWE-bench Verified** 的一批 change 任务上,以 **固定模型** 跑 milky-frog
HEAD,测量 **Harness** 在真实 change 任务里的读文件效率(read-noise)。目的是
**characterize baseline**——确认我们的度量能在外部仓库上复现「读噪音」这一
Harness 病症,为后续的 Harness 改动提供可信的对照基线。

## 核心立场:测的是 Harness,不是 Model

被测变量是 **Harness 设计质量**,Model 是 **被钉死的干扰因子(nuisance
factor)**,不是评估对象。由此推出两条贯穿全文的纪律:

1. **任何绝对分数主要反映 Model 能力**,只有「同一 Model 下、不同 Harness 之间
   的 delta」才是 Harness 信号。Pilot 只有单臂(baseline),所以本轮 **不产出
   可对外比较的绝对分**;它只回答「度量是否可信、病症是否复现」。
2. **按 Harness 敏感度给指标排序**(见下)。Harness 几乎完全掌控的指标做
   headline;Model 主导的指标(任务是否解出)降级为 gate。

诚实的边界:效率指标本质是 **联合产物**——更强的 Model 自己就读得更少,无法
完全隔离 Harness。我们能诚实主张的因果只有一句:**「固定 Model 下,这个 delta
归因于 Harness 改动」**,即一个受控 A/B。Pilot 先把这个 A/B 的基线做扎实。

## 范围

**In scope**:change 任务上的 read-noise,单臂 characterization。

**明确 Out of scope(各有归属,不要混入本 pilot)**:
- **Memory**:是 *cross-Run* 能力,单-Run 语料测不了,需要「episode(同仓 ≥2 个
  相关 Run)」这个新单元。列为 **follow-on track**,read-noise pilot 完成后再做。
- **模糊/探索类 prompt 的全仓扫读**(`你好`→14 读):Langfuse 里最严重的形态,但
  它由 prompt 的模糊性触发,SWE-bench 的精确 issue 不会复现它。这是 **另一条 track**
  (需要在自有仓上用模糊 prompt 语料),不是本 pilot 的失败。
- **端到端正确性(解题率)**:那是 Model 排行榜,不是 Harness benchmark。

## 数据集

### 来源:SWE-bench Verified

Rationale(为什么是它,而非自挖 git 历史或公开 exercise 集):
- read-precision 只有在 **仓库足够大、噪音才可能发生** 时才有区分度。自包含的
  exercise(Aider polyglot / HumanEval)答案只在单文件,噪音面≈0,**直接淘汰**。
- SWE-bench 的 gold patch 是 **人工校验** 的 change,天然提供「相关文件」ground
  truth,规模远超自有 git log,且是领域标准——rationale 站得住。
- Python 与我们自身技术栈一致。
- 关键:**read-noise 不需要跑 SWE-bench 的测试 harness**。只消费
  `base_commit` + `problem_statement` + gold patch 的改动文件,绕开容器/测试执行
  这套最重的机器,只继承它的 ground truth 与可信度。

### 采样:stratified-and-filtered,40,固定 seed

不能裸随机——Verified 里 django 占 40%+,裸抽 40 个可能一半是 django,那测的是
「读 django 有多吵」而非普遍效率。

1. **先 filter**:只保留 gold patch 触及 **1–6 个非测试 source 文件** 的实例
   (沿用旧 `mine_change_tasks.py`(已删除)的 `MIN_SRC_FILES=1 / MAX_SRC_FILES=6`
   bounds,现由 `evals/read_noise/sample.py` 实现),丢弃
   test-only、rename churn、大范围 patch——patch 一散,「相关邻域」就不再清晰,
   footprint ratio 失去意义。
2. **再 stratify**:每仓 **≤6** 个任务,**≥6** 个不同仓,**固定随机 seed** 保证
   可复现。
3. **规模 40**:够大到 per-repo cap 能给出跨仓信号,够小到能反复重跑做调参。
   Harness 可信后再扩到 filtered-Verified 全集。

写进 rationale 的一句:*「子集是 stratified-and-filtered 而非随机,因为
footprint ratio 只在 tight-patch 任务上有意义,只在跨仓时可泛化。」*

## 指标

### 抛弃 `scoring.py` 的邻域法

> `scoring.py` / `test_scoring.py` / `review_tasks.py`(邻域法 + `also_in_scope`
> 人工curation)与 `mine_change_tasks.py`(自有仓 git 挖掘)**已删除**;本 pilot
> 由 `evals/read_noise/`(`sample.py` + `score.py`)取代。以下保留其设计
> 教训。

原 `scoring.py` 用「改动文件所在目录 = in-scope」+ 人工 `also_in_scope` 逃生舱。
它在自有仓能用,但在 40 个陌生大仓上:(1) 合理的上下文阅读(读父类、caller、
相关测试——gold patch 不改它们)会被判成噪音,**惩罚正确行为**;(2) 人工标注 40
个任务的合理上下文集不可扩展。这套复杂度只是为了救「邻域」这个启发式——所以
**整体抛弃**。

代价(睁眼选择):volume 分不清「读 10 个都在目标附近」与「读 10 个散落全仓」。
对一个 **比较型效率 benchmark**,我们不关心散布本身——同等 recall 下读得更少就是
目标,散布只是机制。

### Headline:read-footprint ratio

```
footprint_ratio = distinct_files_read / |gold_source_files|
```

- 分母是 **任务内在** 量(这个任务到底需要碰几个文件),不是 agent 行为,**不可
  gaming**、跨任务可比。
- 1.0 = 恰好读了必要 footprint;9.0 = 读了 9 倍冗余。
- 它是 `read-noise-problem.md` 里 **reads/edit ≈ 9.0** 的直系后代,但把分母从
  「agent 自己的 edit 数(agent 可控、会 gaming)」换成任务内在的 gold 文件数。
- 病症复现问题因此变得清晰可证伪:**footprint ratio 是否显著 > 1.0 且有 spread?**
- 重复读 **不计入** footprint(它是独立的 waste 指标)。

### 按 Harness 敏感度排序的指标表

| 指标 | 谁掌控 | 角色 |
|------|--------|------|
| **footprint ratio** | 混合(scaffolding 塑形,Model 判断) | **headline** |
| `read_file` 读目录报错 / `.env` 试探 | **≈纯 Harness**(工具人体工学) | **headline(unambiguous waste)** |
| 重复读同一文件 | ≈Harness(缺「已读」记忆),但有正当例外 | **soft 诊断,不作优化目标** |
| 首个 edit 前的读数 | 混合 | soft 诊断 |
| 任务是否解出 / recall | **Model 主导** | **仅作 gate** |

**Waste 指标的一个已知坑(务必记住)**:所有 waste 指标都是 **无下界的**——
「读一个文件就放弃」在每个 waste 指标上都拿满分。孤立优化 waste = 奖励偷懒,是
read-noise 的镜像失败。所以 waste 指标 **永远只在 progress gate 之后才有意义**。
另外两项并非真正 unambiguous:重复读有正当来源(读到 truncated 后重读——正是我们
另一条 eval track;edit 后回读校验),首个-edit-前读数在陌生难任务上是正确的
comprehension。故只把 **读目录报错 + 敏感路径试探** 标为 unambiguous waste,
重复读 / edit 前读数降级为 soft。

### Progress gate:recall + right-file-edit

footprint ratio 与 waste 都必须 gate 在「agent 确实有进展」上,否则度量无下界。
- **gate 定义**:agent 读到 **且** 改到了 gold patch 的 source 文件(复用
  `relevant_hit`)。
- 弱点(已知):改对文件 ≠ 改对内容。但作为 **防偷懒的下界** 已经够用——你无法
  「既零 waste 又改到了 gold 文件」。
- **后续升级为 test-harness gate(B)**:等 Harness 与流水线可信后,重新引入
  SWE-bench 测试,仅作 pass/fail 布尔 gate(不作 headline),把 waste 指标 gate
  在 *真正改对* 上。本 pilot 先用 recall + right-file-edit(A)。

## 采集

复用现成接缝,无需改 Harness:订阅只读 `EventHub` 的 `RunAfterTool`,按 `run_id`
收集 `read_file` / `edit_file` 的 path 与 is_error(见 `evals/read_collector.py`、
`evals/tool_collector.py`)。进程内算分。

## 运行方法

### 一个 SWE-bench 实例 → 一个 Run

- **Workspace**:`git clone <repo>` + `git checkout <base_commit>`(不跑测试 ⇒ 不
  需要容器/构建),按仓缓存,避免把 django clone 15 次。
- **Prompt**:`problem_statement` **原样** 作为 Run 目标。不加提示、不加「高效阅读」
  的 nudge——那会污染我们要 characterize 的 baseline。
- **Model / temp**:eval config 里 **显式钉死**,用 shipped 默认 temp(measure 用户
  真实拿到的行为,不用理想化的 temp=0),整轮同一个 Model。

### 停止条件(直接决定 headline,是个 confound)

`max_model_calls` 就是「完成」的定义,是潜伏变量:
- 太低 → agent 被截断 → 每个任务 recall 都低 → gate 处处失败 → 什么都
  characterize 不了(会误判成「Harness 坏了」)。
- 太高 → 不再约束 runaway 扫读,而那正是要测的行为。
- 更关键:「读到完成的读数」只在 **自然完成的 run** 上有意义。撞到 cap 的 run 读数
  被人为截断。

**方案**:
- `max_model_calls` 设 **宽松**(~25–30,高于实测最坏的 22),让 cap 几乎不触发,
  观测 **自然** 读量。
- **按终止原因分区**:footprint / recall / waste **只在自然完成的 run** 上计算;
  撞 cap 的 run **单列** 为一种失败形态上报——撞墙本身是信号,不是拿去平均的数据点。

### 重复与聚合

- 每任务 **N=3** 次重复,per-task 取 **中位数**(对偶发的 22-文件扫读稳健)。
- Pilot 是单臂,不做 paired A/B;但保留 N 是为了看 **run 间方差**(度量是否够稳)。
- 后续 treatment 臂到位时,升级为「同 40 任务 + 同 seed,baseline vs treatment 的
  per-task delta + sign test / bootstrap CI」——paired 比 mean-vs-mean 强得多。

## 成功判据(pilot 通过 = 度量可信,而非命中某个阈值)

**不** port Langfuse 的绝对数(reads/edit≈9、dir-error≈10%…)——任务类型 + 仓库
都变了,那些数无效。改用 **construct validity**:

1. **纯-Harness waste 指标(读目录报错、敏感路径试探)在外部 change 任务上可测且
   非零**——即病症在自有仓之外确实存在。
2. **footprint ratio 有区分度**——40 个任务上有真实 spread、且中位显著 > 1.0,
   不是一条平线。(因为分母已按 gold-patch 大小归一,spread 是行为噪音而非任务
   大小差异。)
3. **人工抽检 ~K 个 run**,确认度量标为 waste 的,人也认为是 waste。

加分项:footprint ratio 与 recall 在固定 progress 下 **负相关**(吵的 run 读更多却
没多找到)——度量确实抓住了「噪音」的更强证据。

## 已知 caveat

- 效率指标是 Model/Harness 联合产物,只能主张「固定 Model 下的 delta 归因 Harness」。
- 邻域/散布信息被 footprint ratio 丢弃(睁眼选择)。
- right-file-edit gate 不保证改对内容(留给后续 test-harness gate)。
- SWE-bench 精确 issue 不复现模糊-prompt 扫读(那是另一条 track)。

## 后续(不属于本 pilot)

1. **Treatment 臂**:baseline 可信后,设计具体 Harness 改动(如「已读文件」记忆、
   `list_dir`/`read_file` 人体工学修复、grep-first),做 paired A/B。
2. **Test-harness gate(B)**:把 waste 指标 gate 在真正解出上。
3. **Memory episode track**:episode = 同仓 ≥2 相关 Run,先用 **oracle 注入 memory**
   测 *use*(隔离 write 质量),复用同一套 read-efficiency 货币;再做端到端自积累。
