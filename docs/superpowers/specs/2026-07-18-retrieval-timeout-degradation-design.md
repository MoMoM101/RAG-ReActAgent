# 检索超时降级与 Reranker 预热设计

> 状态：已 Review，可进入实现计划阶段
>
> 日期：2026-07-18
>
> 范围：rerank 内部超时降级、rerank 候选数削减、reranker 加载预热、reranked 标志修正
>
> 决策：检索永远返回结果——rerank 超时或失败时降级为 RRF 排序结果，绝不因精排慢而返回空

## 1. 背景与问题

生产环境(CPU 推理)实测发现两个问题:

1. **重启后首次检索必超时返回空**。`SearchDocsTool` 将整个 `hybrid_search`(query embedding + 向量检索 + BM25 + RRF 融合 + rerank)包在一个 10s 的 `asyncio.wait_for` 中(`agent/tools.py`,`rag_timeout_retrieval=10.0`)。reranker 模型加载完成后 torch 首次推理需额外数秒惰性初始化,首个真实请求的 rerank 耗时 10s+,触发外层超时。超时处理丢弃**全部**检索结果返回 `count=0`,而其中向量/BM25 检索 1~2s 已完成。Agent 因此回答"知识库没找到"。

2. **热身后 rerank 仍为常态瓶颈**。CPU 上 `bge-reranker-v2-m3` 精排 16 个候选耗时 4~6s,整体检索 6.7s,贴近 10s 上限,余量脆弱。

现有代码已具备部分降级基础:`hybrid_search` 对 rerank **异常**有降级(回退 RRF 排序,标记 `fallback_reason="rerank_fallback"`),reranker 未 ready 时返回中性分数;缺失的是对 rerank **超时**的处理与加载后预热。

## 2. 目标与非目标

### 2.1 目标

- 任何情况下检索都返回结果;最坏情形是未精排的 RRF 排序结果,而非空;
- rerank 单独设预算(默认 5s),超时立即降级,外层 10s 超时仅作最后防线;
- 日常 rerank 耗时降至预算内(候选数 16→8,CPU 耗时约减半);
- reranker "ready" 语义从"模型加载完"改为"可快速服务"(含首次推理预热);
- 工具返回的 `reranked` 标志反映**本次结果**是否真的精排过。

### 2.2 非目标

- 不改前端展示降级提示(`fallback_reason` 已在结果中,前端消费与否不在本期);
- 不做 GPU 检测或自动设备选择;
- 不做 rerank 结果缓存;
- 不调整外层 `rag_timeout_retrieval=10.0`。

## 3. 设计

### 3.1 Rerank 内部超时降级(核心)

- 新增配置 `rag_timeout_rerank: float = 5.0`(`config.py`,可被 `.env` 覆盖);
- `rag/retriever.py` 的 `hybrid_search` 中,对 `_rerank_results(...)` 调用包 `asyncio.wait_for(..., timeout=settings.rag_timeout_rerank)`;
- `asyncio.TimeoutError` → 复用现有降级路径:取 RRF 排序前 top_k,`fallback_reason` 标记 `rerank_timeout`(与现有 `rerank_fallback` 并列,便于日志区分超时与异常);
- 现有 rerank 异常降级行为不变。

### 3.2 候选数削减

- `rerank_top_n` 默认值 16 → 8(`config.py`);
- 最终 `retrieval_top_k=8`,8 个精排候选仍覆盖全部输出位;RRF 融合候选池 `rrf_count = max(rerank_top_n, top_k*3)` 逻辑不变。

### 3.3 Reranker 预热

- `reranker/cross_encoder.py` 的 `preload_async._load()` 中,`CrossEncoder(model)` 构造完成后、置 `_ready=True` 之前,执行一次 dummy 推理:`self._model.predict([["warmup", "warmup"]])`;
- 预热在既有后台线程内进行,不阻塞启动;预热期间检索照常(未 ready → 中性分数,等效 RRF 排序);
- 预热失败按现有加载失败路径处理(`set_reranker_failed`);
- 语义变化:`/api/health/dependencies` 中 reranker 显示 ready 的时刻推迟数秒,反映真实可服务状态。

### 3.4 `reranked` 标志修正

- `agent/tools.py` 中 `"reranked": is_reranker_ready()` 改为从本次结果推导:结果带 `rerank_timeout`/`rerank_fallback` 标记,或 reranker 未 ready → `false`;
- 消除"全局 ready 但本次已降级仍报 true"的误报。

## 4. 数据流(降级路径)

```
query → embedding → 向量检索 + BM25 → RRF 融合 → dedup/过滤
  → rerank (预算 5s)
      ├─ 按时完成 → 精排结果, reranked=true
      ├─ 超时     → RRF 排序 top_k, fallback_reason=rerank_timeout, reranked=false
      └─ 异常     → RRF 排序 top_k, fallback_reason=rerank_fallback, reranked=false
  → 返回(永不为空,除非检索本身无命中)
```

## 5. 测试计划(TDD)

1. rerank 超时 → 返回 RRF 结果 + `fallback_reason="rerank_timeout"`(注入 sleep 超预算的 fake reranker);
2. rerank 正常 → 结果精排,无 fallback 标记;
3. 预热:dummy 推理完成前 `_ready` 为 False,完成后为 True;
4. `SearchDocsTool` 降级时 `reranked=false`,正常时 `true`;
5. 回归:现有 `tests/rag/`、`tests/agent/test_tools.py` 全绿。

## 6. 涉及文件

| 文件 | 改动 |
|------|------|
| `backend/config.py` | 新增 `rag_timeout_rerank=5.0`;`rerank_top_n` 16→8 |
| `backend/rag/retriever.py` | rerank 调用加 `wait_for` 超时降级,新增 `rerank_timeout` 标记 |
| `backend/reranker/cross_encoder.py` | 加载后 dummy 推理预热,再置 ready |
| `backend/agent/tools.py` | `reranked` 标志从结果 fallback 状态推导 |
