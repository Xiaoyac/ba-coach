# 本地向量 RAG：第一阶段

## 交付范围

实现本地多语言 embedding、Qdrant 持久化向量索引、P0 / 纯向量 / 混合检索三路对比，以及额外的语义挑战评测集。

此阶段是独立本地实验入口。没有更改业务表、导入线上知识、连接生产服务器或切换线上检索。现有应用仍使用 P0。新检索类提供相同的异步 `search(module, query, top_k)` 接口，但没有接入生产 singleton；需要第二阶段的数据同步、超时降级和部署验证后再接入。

## 实现方式

- 原文：复用 `evaluate_retrieval.load_corpus()`，从项目知识文件读取 1,331 个片段，保留原有 Markdown 分块和模块分类；不打开业务数据库。
- 模型：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，FastEmbed 0.7.4 / ONNX CPU，384 维。选轻量多语言模型作为本机基线，并不声称优于 BGE-M3。模型仅首次下载，知识正文和问题在本机计算，不发送模型 API。
- 长文本：按实际 tokenizer 的 token offsets 分窗，窗口最多 120 tokens，再平均池化、归一化。保留完整正文返回 Prompt；防止长片段尾部被静默截断。代价是多主题正文可能稀释局部语义，下一阶段应比较更好的语义分块与子片段检索。
- 存储：Qdrant 客户端本地持久化模式，默认 `backend/.rag-local/qdrant`，不需要 Docker 或开放端口。这个模式使用精确搜索，适合当前本地实验；不是独立生产 Qdrant 服务，也不能多进程共享同一路径。
- 版本：将原文、来源、分类、ID、模型资产哈希、维度和分窗策略计算为 collection 指纹。原文或模型变化会使用新 collection，不会混用旧向量。批量构建结束才写 ready 标记；数量校验不通过或构建未完成时拒绝查询。
- 模块过滤：BA / PA / BCT / MI 范围条件放入 Qdrant 查询。ready 标记没有分类，不参与候选。
- 关键词通道：沿用 P0 分数与覆盖度、相对分数和每来源限制。
- 向量通道：余弦相似度门槛初始为 0.5，取 20 个候选。该值仅为实验参数，不代表 50% 正确率，未经过业务校准。
- 混合：两路候选按 RRF 融合，`score = Σ 1 / (60 + rank)`，同一路重复 ID 只计一次。最终每个来源最多 2 条，数量遵循调用方 top_k；不会把词法覆盖度作为语义候选的强制门槛。
- 本阶段没有 reranker。RRF 是排名融合，不是重新理解问题和正文的模型精排。

例如“迈不开第一步”与“降低活动启动难度”可通过向量通道匹配；同时“我不想跑步”也可能与跑步知识相似，因此保留否定表达和空结果测试，不默认认为向量检索一定改善结果。

## 本机运行

在项目的 `backend` 目录打开 PowerShell。全部路径默认指向本机，不使用 DATABASE_URL。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
.\.venv\Scripts\python.exe scripts/rag_local.py build
.\.venv\Scripts\python.exe scripts/rag_local.py query --mode hybrid --module module_2 --query "我不想跑步，想改成游泳。"
.\.venv\Scripts\python.exe scripts/rag_local.py evaluate --split dev --output evals/vector_dev_report.json
.\.venv\Scripts\python.exe scripts/rag_local.py evaluate --split holdout --output evals/vector_holdout_report.json
.\.venv\Scripts\python.exe scripts/rag_local.py evaluate --cases evals/retrieval_vector_cases.json --split all --output evals/vector_stress_report.json
```

下载缓存完整后，可在当前 PowerShell 设置 `$env:HF_HUB_OFFLINE='1'`，避免模型加载时联网检查。离线设置不会下载缺失模型。

通过 `--mode p0` / `--mode vector` / `--mode hybrid` 切换查询方式。评测命令始终比较三路。通过 `--vector-min-score 0.5` 和 `--candidates 20` 调整实验参数；只在 dev 调参，holdout 用于冻结参数后的检验。

`--model` 接受已安装 FastEmbed 支持的文本模型名称；更换后需要先执行 build。模型内部输入规范和平均分窗策略仍需单独验证，不保证任意新模型即插即优。

重复 build 会验证 ready 标记和数量，完整时复用索引。失败后重跑会覆盖同版本对应点并完成剩余构建；不会删除其他版本。旧 collection 的回收与生产级原文更新/删除同步不在本阶段范围内。

## 评测如何保证可比较

三路共享知识片段、查询构造、模块范围和输出数量。复用 `_knowledge_query()` 的近期对话 / 目标卡处理。原有 88 条用例保持不变，新增 12 条保存在单独的 `retrieval_vector_cases.json`，覆盖口语改写、中英文、否定、多轮及无关问题。

相关性标签在检索之前解析为片段 ID；标签对不上原文直接报错。报告保存知识清单、模型指纹、数据集哈希、代码哈希、参数、逐条结果与分类汇总。

指标包括 Hit@K、Recall@K、返回结果精度、MRR、nDCG、正确空结果率与耗时。向量和混合检索各自实际执行一次查询 embedding，耗时不含模型初始化与离线构建。空结果率与有答案查询召回率应同时看，不能仅比较召回率。

这些标签由助手编写，尚待领域人员复核；检索评测不是回答质量或临床效果验证。本阶段不调用生成模型。

## 本次实际验证（2026-09-07）

真实模型已下载并完成全部 1,331 个片段的 embedding 和持久化。构建耗时 456.95 秒（不含模型下载/初始化）。再次执行 build 返回 `built: false`，验证已完成索引能够直接复用。

初始完整后端回归 313 项通过；随后补充持久化复用、P0 等价性与长文本尾部测试，最终向量专项 12 项全部通过。依赖检查 `pip check` 通过。全量测试中只有既有 Starlette 弃用警告和后续已消除的本地 Qdrant 搜索参数警告。

评测均固定使用余弦门槛 0.5，未根据 holdout 调参。结果如下，百分比按当前初始标注计算：

| 数据集 | 方法 | 正例 Hit@K | 负例正确返回空 | 返回结果平均精度 | 平均查询耗时 |
| --- | --- | --- | --- | --- | --- |
| Dev：28 正例 / 16 负例 | P0 | 92.86% | 81.25% | 47.04% | 19.8 ms |
| Dev | 纯向量 | 42.86% | 62.50% | 15.86% | 58.6 ms |
| Dev | 混合 | 92.86% | 50.00% | 31.94% | 81.1 ms |
| Holdout：28 正例 / 16 负例 | P0 | 92.86% | 56.25% | 46.90% | 18.3 ms |
| Holdout | 纯向量 | 60.71% | 68.75% | 27.42% | 58.4 ms |
| Holdout | 混合 | 96.43% | 50.00% | 33.10% | 80.1 ms |
| 新增挑战：8 正例 / 4 负例 | P0 | 12.50% | 100% | 5.00% | 21.4 ms |
| 新增挑战 | 纯向量 | 50.00% | 100% | 27.38% | 80.8 ms |
| 新增挑战 | 混合 | 50.00% | 100% | 15.62% | 90.3 ms |

原始报告：[Dev](backend/evals/vector_dev_report.json)、[Holdout](backend/evals/vector_holdout_report.json)、[语义挑战](backend/evals/vector_stress_report.json)。挑战集很小且专门测试语义难例，不能据此推断真实流量提升幅度。

观察：混合检索在 holdout 多命中 1 个正例，但精度下降，且多误返 1 个负例。语义挑战中，“How can friends provide social support when I try to change my habits?” 可以经混合检索命中中文“社会支持”片段，P0 未命中。与此同时混合检索也会引入其他无关片段。

因此第一阶段证明了链路可运行、可复现以及语义通道的补召回价值，尚未证明这个模型和融合参数可以整体替代 P0。下一轮应在 dev 上比较更强的多语言检索模型、分块方案与阈值；第二阶段再加入 reranker，并重新评测精度与空结果能力。

## 线上接入边界

本地 corpus ID 由文件枚举生成，不代表线上业务库的 chunk 主键。不得把这个本地索引直接绑定线上数据库片段 ID。

当前不包含多进程服务、reranker、查询故障自动降级、在线增量同步、灰度切换或生产运维。第一阶段查询失败会显式报错，避免在评测中用 P0 回退掩盖向量检索故障。模型缓存与索引已加入 `.gitignore`。

进入第二阶段前，应根据本次评测选择模型/阈值/分块策略，再补齐真实数据库来源映射、更新删除同步、模型服务超时、访问范围校验、索引发布与回退。
