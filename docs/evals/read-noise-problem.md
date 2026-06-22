# 问题分析:读文件噪音过多 (Read-Noise)

> 数据来源:Langfuse 导出
> `1782082382924-lf-events-export-cmqmdi01w01z3ad0eezp26ngb.jsonl`
> (29 个 trace / Run,420 个 observation)。
> 本文是评估流程的「问题定义」前置文档,evals 设计见同目录后续文档。

## 一句话

奶蛙在接到任务后会对仓库做大范围扫读,读进来的文件大部分与任务无关(噪音),
并伴随**重复读**和**对目录误用 `read_file`** 两类浪费。

Langfuse 已经把每次 `read_file` 的 `input={"path": ...}` 逐条记录,
所以「读了谁、读了几次、哪些读失败」可以从 trace 100% 还原 —— 这也是评估能落地的基础。

## 量化证据(本次导出)

只统计真正做了工具调用的 18 个 Run:

| 指标 | 数值 |
|------|------|
| read_file 调用总数 | 135 |
| edit_file 调用总数 | 15 |
| **reads / edit 比** | **9.0**(读 9 个文件才改 1 个) |
| 每个 Run 读文件数 | 中位数 5,均值 7.5,**最多 22** |
| 只读不改的 Run | **15 / 18** |
| 重复读(同一文件读 ≥2 次) | 10 次,占 7% |
| 读失败 / 把目录当文件读 | 13 次,占 10% |

工具调用分布:`read_file` 135、`list_dir` 75、`edit_file` 15、`write_file` 1。

## 噪音的四种典型形态(均有 trace 实证)

### 1. 琐碎/探索性提问触发全仓扫读(最严重)

- prompt `你好` → 读了 **14 个文件**(handlers、models、checkpoint、sandbox…),零 edit。
- prompt `帮我看一下你是如何计算token的` → 读了 **22 个文件**,把整个 `ui/presenter/*`、
  `cli/*` 都读了,而答案只在 `models/openai.py` + `domain.py` + `ui/usage.py`。
- prompt `你觉得我们工具类实现的好吗` → 读了 **19 个文件**,几乎遍历 `harness/tools/` 全树。

读取量与任务实际需要严重不成比例:一句问候不该触发整库扫描。

### 2. 对目录误用 `read_file`

agent 反复对**目录**调用 `read_file`,得到 `not a file` 报错后才转用 `list_dir`:

```
read_file("src/milky_frog/harness")        -> not a file
read_file("src/milky_frog/checkpoint")     -> not a file
read_file("src/milky_frog/models")         -> not a file
read_file("src/milky_frog")                -> not a file
```

这类「目录当文件读」是 13 次读失败(10%)的主体 —— 纯浪费的调用与 token。

### 3. 重复读同一文件(缺乏「已读」记忆)

- trace `add32120`(`帮我看看steering的实现`):`tests/test_steering.py` 读 3 次、
  `tests/test_harness.py` 读 3 次、`runner.py` 读 2 次 —— 9 次读只覆盖 4 个文件。
- trace `1329dee5`:`pyproject.toml` 读 2 次。

agent 不记得自己读过什么,在同一个 Run 内反复回读。

### 4. 试探敏感路径

trace `add32120` 出现 `read_file("/Users/.../.env", limit=3)`,被 sandbox 拦下
(`sensitive path requires approval`)。拦截生效是好的,但模型**主动尝试读 .env**
本身是一次无效且不该发生的调用。

## 数据集的重要 caveat

本次导出里 **15/18 个 Run 是只读问答(Q&A),不是改代码任务**
(`你好`、`没有用titoken吗`、`你觉得…好吗` 等)。
真正的 change 任务只有 2 个(`522b9e1d` 11 读/8 改、`add32120` 9 读/5 改)。

含义:

- 这份日志足以**证明「读噪音」现象存在且严重**,尤其在探索/问答场景。
- 但用户的核心抱怨是「**叫他改代码**时读一堆噪音」,而本导出几乎没有 change 任务样本。
- → 评估数据集必须**专门构造 change 任务**(带 ground-truth 相关文件集),
  不能只靠这份历史日志。

## 对评估设计的直接结论

1. **主指标:Read Precision** = 读过的文件里属于「相关文件」的比例(低 = 噪音多)。
   配套 Read Recall 防止「优化成少读但漏读」。
2. **辅助指标**(都能从 trace 直接算,无需改 Harness):
   `reads_per_edited_file`、重复读次数、读失败率(目录/敏感路径)、首个 edit 前的读数。
3. **采集接缝**:订阅只读 `LifecycleBus` 的 `RunAfterTool`,按 `run_id` 收集
   `read_file` / `edit_file` 的 path,在进程内算分,再把分数回写 Langfuse scores。
4. **数据集**:构造 change 任务(可用公开 bench 的 gold patch 当 ground-truth,
   或挖本仓 git commit),每个任务跑 N 次看 precision 分布,定回归阈值。
