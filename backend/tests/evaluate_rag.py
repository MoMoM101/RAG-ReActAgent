"""RAG 检索精准度评测脚本

评测维度：
- Precision@k / Recall@k: 精确率与召回率
- MRR (Mean Reciprocal Rank): 首个相关结果的平均倒数排名
- NDCG@k (Normalized Discounted Cumulative Gain): 位置加权相关性
- Hit Rate@k: 前k个结果中至少命中1个的比例
- Semantic vs Keyword 贡献分析
- 重排序影响分析
"""

import asyncio
import io
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure backend is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings


# ── 测试语料定义 ──────────────────────────────────────────────

@dataclass
class TestDoc:
    """测试文档定义"""
    filename: str
    content: str
    file_type: str = ".txt"


@dataclass
class QueryCase:
    """查询用例：query + 标注的相关chunk索引（在文档内的相对序号）"""
    query: str
    relevant_chunk_indices: list[int]  # 0-based chunk indices within the document
    doc_index: int = 0  # 哪个测试文档
    expected_keywords: list[str] = field(default_factory=list)


# ── 3篇测试文档，每篇约500 tokens，覆盖不同领域 ──

TEST_DOCS = [
    TestDoc(
        filename="ml_guide.txt",
        content=(
            "# Python 机器学习完全指南\n\n"
            "Python 是机器学习和数据科学领域最流行的编程语言。\n"
            "它拥有丰富的生态系统，包括 scikit-learn、TensorFlow、PyTorch 等框架。\n"
            "scikit-learn 提供了简单易用的 API，适合传统的分类、回归和聚类任务。\n"
            "TensorFlow 和 PyTorch 则专注于深度学习，支持 GPU 加速训练。\n\n"
            "## 数据预处理\n\n"
            "在训练模型之前，数据预处理是必不可少的步骤。\n"
            "常见的预处理操作包括：缺失值处理（删除或填充）、特征归一化（MinMax 或 Z-score）、\n"
            "类别变量编码（One-Hot 或 Label Encoding）、以及特征工程（创建新特征）。\n"
            "良好的预处理可以显著提升模型性能，通常比调参更重要。\n\n"
            "## 深度学习\n\n"
            "深度学习是机器学习的一个子领域，使用多层神经网络来学习数据的层次化表示。\n"
            "卷积神经网络（CNN）擅长图像识别任务，循环神经网络（RNN）适合序列数据，\n"
            "而 Transformer 架构在自然语言处理领域取得了革命性突破。\n"
            "BERT、GPT 等预训练大模型都是基于 Transformer 架构的。\n\n"
            "## 模型评估\n\n"
            "模型训练完成后，需要使用测试集评估其泛化能力。\n"
            "常用指标包括：准确率、精确率、召回率、F1 分数和 ROC-AUC。\n"
            "交叉验证（Cross-Validation）可以更稳定地估计模型性能。"
        ),
    ),
    TestDoc(
        filename="climate_report.txt",
        content=(
            "# 全球气候变化报告 2024\n\n"
            "## 温度变化\n\n"
            "自工业革命以来，全球平均气温已上升约 1.1°C。\n"
            "2023 年是有记录以来最热的一年，全球多地出现极端高温天气。\n"
            "如果温室气体排放继续以当前速度增长，预计到 2100 年气温将上升 2.5-4.5°C。\n"
            "这将导致海平面上升、冰川融化和生态系统崩溃。\n\n"
            "## 温室气体\n\n"
            "大气中的二氧化碳浓度在 2023 年达到 420 ppm（百万分之一），\n"
            "是过去 200 万年来的最高水平。甲烷浓度也在持续上升。\n"
            "主要排放源包括：化石燃料燃烧（占 75%）、工业生产（占 15%）和农业活动（占 10%）。\n\n"
            "## 可再生能源\n\n"
            "太阳能和风能是全球增长最快的能源形式，年均增长率约 20%。\n"
            "2023 年全球可再生能源装机容量突破 3,000 GW，其中中国占全球新增装机的 50% 以上。\n"
            "光伏发电成本在过去十年下降了 90%，已低于化石燃料。\n"
            "储能技术的进步正在解决可再生能源的间歇性问题。\n\n"
            "## 应对措施\n\n"
            "《巴黎协定》目标是控制全球升温在 2°C 以内，力争 1.5°C。\n"
            "碳捕获与封存技术（CCS）虽然前景广阔，但目前成本高昂。\n"
            "个人可以通过减少碳足迹、选择绿色出行和节约能源来贡献力量。"
        ),
    ),
    TestDoc(
        filename="mediterranean_cooking.txt",
        content=(
            "# 地中海美食烹饪指南\n\n"
            "## 意大利经典：Carbonara\n\n"
            "正宗的意面 Carbonara 只需要四种核心食材：\n"
            "鸡蛋黄、风干猪脸肉（guanciale）、佩科里诺罗马羊奶酪（pecorino romano）和黑胡椒。\n"
            "关键步骤：将鸡蛋和奶酪搅拌成奶油状酱汁，猪脸肉煎至金黄，\n"
            "意面煮至 al dente，趁热将所有材料快速拌匀。\n"
            "切忌使用奶油——意大利人认为这是对传统的亵渎。\n\n"
            "## 希腊沙拉\n\n"
            "传统希腊沙拉（Horiatiki）由以下食材组成：\n"
            "新鲜番茄切块、黄瓜切片、红洋葱圈、大块菲达奶酪（feta）、卡拉马塔橄榄（Kalamata olives）。\n"
            "用特级初榨橄榄油、红酒醋、干牛至和盐调味。\n"
            "与普通沙拉不同的是，希腊沙拉不使用生菜叶作为基底。\n\n"
            "## 西班牙海鲜饭\n\n"
            "瓦伦西亚风格的西班牙海鲜饭（Paella Valenciana）使用：\n"
            "Bomba 圆粒米、藏红花（saffron）、鸡肉、兔肉、扁豆和蜗牛（传统版本）。\n"
            "海鲜版本（Paella de Marisco）则用虾、贻贝、鱿鱼等代替肉类。\n"
            "关键烹饪工具是宽而浅的平底锅（paellera），使米饭均匀受热形成底部焦香的 socarrat。"
        ),
    ),
]

# ── 10 个查询用例 + 标注 ──

QUERY_CASES = [
    # Document 1: ML Guide (chunk 0-3)
    QueryCase(
        query="Python 机器学习用什么框架",
        relevant_chunk_indices=[0],  # scikit-learn, TensorFlow, PyTorch
        doc_index=0,
        expected_keywords=["scikit-learn", "TensorFlow", "PyTorch"],
    ),
    QueryCase(
        query="数据预处理有哪些方法",
        relevant_chunk_indices=[1],  # 缺失值处理、特征归一化
        doc_index=0,
        expected_keywords=["缺失值", "归一化", "编码"],
    ),
    QueryCase(
        query="什么是深度学习",
        relevant_chunk_indices=[2],  # 多层神经网络
        doc_index=0,
        expected_keywords=["多层神经网络", "CNN", "Transformer"],
    ),
    QueryCase(
        query="模型评估用什么指标",
        relevant_chunk_indices=[3],  # 准确率、F1
        doc_index=0,
        expected_keywords=["准确率", "F1", "ROC-AUC", "交叉验证"],
    ),
    # Document 2: Climate Report (chunk 0-3)
    QueryCase(
        query="全球气温上升了多少度",
        relevant_chunk_indices=[0],  # 1.1°C
        doc_index=1,
        expected_keywords=["1.1°C", "温度上升"],
    ),
    QueryCase(
        query="大气中二氧化碳浓度是多少",
        relevant_chunk_indices=[1],  # 420 ppm
        doc_index=1,
        expected_keywords=["420 ppm", "二氧化碳"],
    ),
    QueryCase(
        query="太阳能和风能的增长情况",
        relevant_chunk_indices=[2],  # 20%
        doc_index=1,
        expected_keywords=["20%", "可再生能源", "光伏"],
    ),
    # Document 3: Cooking (chunk 0-2)
    QueryCase(
        query="意面 Carbonara 怎么做",
        relevant_chunk_indices=[0],  # 鸡蛋黄、guanciale
        doc_index=2,
        expected_keywords=["guanciale", "pecorino", "鸡蛋黄"],
    ),
    QueryCase(
        query="希腊沙拉需要什么食材",
        relevant_chunk_indices=[1],  # 番茄、菲达奶酪
        doc_index=2,
        expected_keywords=["菲达", "feta", "卡拉马塔"],
    ),
    QueryCase(
        query="西班牙海鲜饭怎么烹饪",
        relevant_chunk_indices=[2],  # Bomba米、藏红花
        doc_index=2,
        expected_keywords=["Bomba", "saffron", "socarrat", "paellera"],
    ),
]


# ── 评测指标计算 ──────────────────────────────────────────────

def precision_at_k(relevant: set[int], retrieved_count: int, k: int) -> float:
    """Precision@k: top-k中相关结果占比"""
    # relevant 是chunk级别的索引集合，无法直接映射到retrieved排名
    # 使用标签：每个retrieved结果标记是否相关
    return 0.0  # 占位，实际在主流程中计算


def compute_metrics(
    retrieved_chunks: list[str],  # 检索到的文本列表
    ground_truth_texts: list[str],  # 标注相关的文本列表
    k_values: list[int] = (3, 5, 10),
) -> dict:
    """计算所有标准 IR 评测指标"""
    # 相关性判定：用 Jaccard 相似度（word-level）检测检索文本是否命中标注
    def is_relevant(text: str, gt_texts: list[str]) -> bool:
        if not text.strip():
            return False
        text_words = set(text.lower().split())
        for gt in gt_texts:
            gt_words = set(gt.lower().split())
            if not gt_words:
                continue
            intersection = text_words & gt_words
            union = text_words | gt_words
            jaccard = len(intersection) / len(union) if union else 0
            # 高重叠 → 命中；或 gt 较短且大部分词在 text 中
            if jaccard >= 0.25 or (len(gt_words) <= 15 and len(intersection) / max(len(gt_words), 1) >= 0.5):
                return True
        return False

    n_relevant = len(ground_truth_texts)

    # Initialize results with all k-value keys (always, even if n_relevant == 0)
    results = {"precision": {}, "recall": {}, "mrr": 0.0, "ndcg": {}, "hit": {}}
    for k in k_values:
        results["precision"][k] = 0.0
        results["recall"][k] = 0.0
        results["ndcg"][k] = 0.0
        results["hit"][k] = 0

    if n_relevant == 0 or not retrieved_chunks:
        return results

    # Binary relevance for each position
    relevance = [1 if is_relevant(chunk, ground_truth_texts) else 0 for chunk in retrieved_chunks]

    # MRR: 1 / rank of first relevant
    for rank, rel in enumerate(relevance, 1):
        if rel == 1:
            results["mrr"] = 1.0 / rank
            break

    for k in k_values:
        top_k = relevance[:k]
        results["precision"][k] = sum(top_k) / k if k > 0 else 0.0
        results["recall"][k] = sum(top_k) / n_relevant
        results["hit"][k] = 1 if sum(top_k) > 0 else 0

        dcg = sum(top_k[i] / math.log2(i + 2) for i in range(len(top_k)))
        ideal_relevances = sorted(relevance, reverse=True)[:k]
        idcg = sum(ideal_relevances[i] / math.log2(i + 2) for i in range(len(ideal_relevances)))
        results["ndcg"][k] = dcg / idcg if idcg > 0 else 0.0

    return results


# ── 主评测流程 ─────────────────────────────────────────────────

async def run_evaluation():
    print("=" * 70)
    print("RAG 检索精准度评测")
    print(f"Embedding: {settings.embedding_provider}/{settings.embedding_model}")
    print(f"Reranker: {'enabled' if settings.rerank_enabled else 'disabled'}")
    print(f"Chunk size: {settings.chunk_size}, Overlap: {settings.chunk_overlap}")
    print(f"Qdrant: {'local' if not settings.qdrant_host else settings.qdrant_host}")
    print("=" * 70)

    from rag.pipeline import ingest_document
    from rag.retriever import hybrid_search, RetrievalResult
    from models.database import async_session
    from sqlalchemy import select, delete
    from models.orm import Document
    from textdb.sqlite_fts import SQLiteFTS5
    from vectordb.factory import create_vectordb

    # ── Step 1: 清理旧数据 ──
    print("\n[1/5] 清理旧测试数据...")
    async with async_session() as session:
        for doc_def in TEST_DOCS:
            import hashlib
            fh = hashlib.sha256(doc_def.content.encode()).hexdigest()
            result = await session.execute(select(Document).where(Document.file_hash == fh))
            existing = result.scalar_one_or_none()
            if existing:
                vectordb = await create_vectordb()
                fts = SQLiteFTS5()
                await vectordb.delete_by_document(existing.id)
                await fts.delete_by_document(existing.id)
                await session.delete(existing)
        await session.commit()
    print("   清理完成")

    # ── Step 2: 摄入测试文档 ──
    print("\n[2/5] 摄入测试文档...")
    doc_ids = []
    for i, doc_def in enumerate(TEST_DOCS):
        content_bytes = doc_def.content.encode("utf-8")
        try:
            doc_id = await ingest_document(
                filename=doc_def.filename,
                file_content=content_bytes,
                file_type=doc_def.file_type,
                background=False,  # 同步处理，确保完成
            )
            doc_ids.append(doc_id)
            print(f"   [{i+1}/{len(TEST_DOCS)}] {doc_def.filename} → {doc_id[:8]}... ✓")
        except ValueError as e:
            print(f"   [{i+1}/{len(TEST_DOCS)}] {doc_def.filename} → SKIP (duplicate)")
            # Find existing doc
            import hashlib
            fh = hashlib.sha256(content_bytes).hexdigest()
            async with async_session() as session:
                result = await session.execute(select(Document).where(Document.file_hash == fh))
                doc = result.scalar_one_or_none()
                if doc:
                    doc_ids.append(doc.id)

    # ── Step 3: 获取标注文本 ──
    print("\n[3/5] 提取标注数据...")
    from rag.splitter import split_text

    doc_chunks = []  # doc_chunks[doc_idx] = [text, text, ...]
    for doc_def in TEST_DOCS:
        chunks = split_text(doc_def.content, settings.chunk_size, settings.chunk_overlap)
        doc_chunks.append([c.text for c in chunks])
        print(f"   {doc_def.filename}: {len(chunks)} chunks")

    ground_truth_texts = {}  # query_idx → [relevant_text, ...]
    for q_idx, qc in enumerate(QUERY_CASES):
        texts = []
        if qc.doc_index < len(doc_chunks):
            avail_chunks = doc_chunks[qc.doc_index]
            n_avail = len(avail_chunks)
            for chunk_idx in qc.relevant_chunk_indices:
                # 如果原始 chunk 索引超出范围（chunk 被合并），映射到最近的可用 chunk
                mapped_idx = min(chunk_idx, n_avail - 1) if n_avail > 0 else chunk_idx
                if mapped_idx < n_avail and avail_chunks[mapped_idx] not in texts:
                    texts.append(avail_chunks[mapped_idx])
        ground_truth_texts[q_idx] = texts

    # ── Step 4: 运行检索评测 ──
    print("\n[4/5] 运行检索评测...")
    k_values = [3, 5, 10]

    all_metrics_no_rerank = []
    all_metrics_rerank = []
    all_results_no_rerank: list[list[RetrievalResult]] = []
    all_results_rerank: list[list[RetrievalResult]] = []
    latencies_no_rerank = []
    latencies_rerank = []

    for j, qc in enumerate(QUERY_CASES):
        gt = ground_truth_texts[j]

        # Without reranker
        t0 = time.time()
        results_no_rerank = await hybrid_search(qc.query, top_k=10, use_rerank=False)
        lat_no = int((time.time() - t0) * 1000)
        latencies_no_rerank.append(lat_no)
        all_results_no_rerank.append(results_no_rerank)
        retrieved_texts = [r.text for r in results_no_rerank]
        metrics_no = compute_metrics(retrieved_texts, gt, k_values)
        all_metrics_no_rerank.append(metrics_no)

        # With reranker (if enabled)
        if settings.rerank_enabled:
            t0 = time.time()
            results_rerank = await hybrid_search(qc.query, top_k=10, use_rerank=True)
            lat_re = int((time.time() - t0) * 1000)
            latencies_rerank.append(lat_re)
            all_results_rerank.append(results_rerank)
            retrieved_texts_re = [r.text for r in results_rerank]
            metrics_re = compute_metrics(retrieved_texts_re, gt, k_values)
            all_metrics_rerank.append(metrics_re)

        print(f"   [{j+1:2d}/{len(QUERY_CASES)}] \"{qc.query[:40]}\" → "
              f"MRR={metrics_no['mrr']:.2f} P@5={metrics_no['precision'][5]:.2f} "
              f"({lat_no}ms)")

    # ── Step 5: 汇总分析 ──
    print("\n[5/5] 汇总分析...\n")
    print("=" * 70)
    print("评测结果汇总")
    print("=" * 70)

    # Aggregation
    def aggregate(metrics_list: list[dict], k_vals: list[int]) -> dict:
        n = len(metrics_list)
        agg = {"precision": {}, "recall": {}, "ndcg": {}, "hit": {}, "mrr": 0.0}
        for k in k_vals:
            agg["precision"][k] = sum(m["precision"][k] for m in metrics_list) / n
            agg["recall"][k] = sum(m["recall"][k] for m in metrics_list) / n
            agg["ndcg"][k] = sum(m["ndcg"][k] for m in metrics_list) / n
            agg["hit"][k] = sum(m["hit"][k] for m in metrics_list) / n
        agg["mrr"] = sum(m["mrr"] for m in metrics_list) / n
        return agg

    agg_no_rerank = aggregate(all_metrics_no_rerank, k_values)
    agg_rerank = aggregate(all_metrics_rerank, k_values) if all_metrics_rerank else None

    # ── 表格输出 ──
    print(f"\n{'指标':<20} {'k=3':>8} {'k=5':>8} {'k=10':>8}")
    print("-" * 50)

    print(f"\n── 无重排序 ──")
    print(f"{'平均延迟 (ms)':<22} {sum(latencies_no_rerank)/len(latencies_no_rerank):7.0f}ms")
    for metric_name, label in [("precision", "Precision"), ("recall", "Recall"),
                                 ("ndcg", "NDCG"), ("hit", "Hit Rate")]:
        vals = agg_no_rerank[metric_name]
        print(f"{label:<20} {vals[3]:>8.2%} {vals[5]:>8.2%} {vals[10]:>8.2%}")
    print(f"{'MRR':<20} {agg_no_rerank['mrr']:>8.2%}")

    if agg_rerank:
        print(f"\n── 含重排序 ──")
        print(f"{'平均延迟 (ms)':<22} {sum(latencies_rerank)/len(latencies_rerank):7.0f}ms")
        for metric_name, label in [("precision", "Precision"), ("recall", "Recall"),
                                     ("ndcg", "NDCG"), ("hit", "Hit Rate")]:
            vals = agg_rerank[metric_name]
            print(f"{label:<20} {vals[3]:>8.2%} {vals[5]:>8.2%} {vals[10]:>8.2%}")
        print(f"{'MRR':<20} {agg_rerank['mrr']:>8.2%}")

    # ── 逐查询明细 ──
    print(f"\n{'─'*70}")
    print(f"逐查询分析")
    print(f"{'─'*70}")
    for j, qc in enumerate(QUERY_CASES):
        results = all_results_no_rerank[j]
        top_texts = [r.text.replace('\n', ' ')[:60] for r in results[:3]]
        gt = ground_truth_texts[j]
        gt_summary = [g.replace('\n', ' ')[:40] for g in gt]

        print(f"\n查询 {j+1}: \"{qc.query}\"")
        print(f"  标注: {gt_summary}")
        print(f"  Top-3 检索结果:")
        for i, (r, t) in enumerate(zip(results[:3], top_texts)):
            source_label = {"semantic": "语义", "keyword": "关键词", "hybrid": "融合"}.get(r.source, r.source)
            print(f"    #{i+1} [{source_label}] score={r.score:.3f} \"{t}\"")

        # 关键词贡献分析
        sem_count = sum(1 for r in results if r.source == "semantic")
        kw_count = sum(1 for r in results if r.source == "keyword")
        hyb_count = sum(1 for r in results if r.source == "hybrid")
        print(f"  来源分布: 语义={sem_count} 关键词={kw_count} 融合={hyb_count}")

    # ── 重排序差异分析 ──
    if agg_rerank:
        print(f"\n{'─'*70}")
        print(f"重排序效果分析")
        print(f"{'─'*70}")
        p5_no = agg_no_rerank["precision"][5]
        p5_re = agg_rerank["precision"][5]
        delta = (p5_re - p5_no) / max(p5_no, 0.01) * 100
        direction = "↑ 提升" if delta > 0 else "↓ 下降" if delta < 0 else "→ 无变化"
        print(f"  Precision@5: {p5_no:.2%} → {p5_re:.2%} ({direction} {abs(delta):.1f}%)")

        mrr_no = agg_no_rerank["mrr"]
        mrr_re = agg_rerank["mrr"]
        delta_mrr = (mrr_re - mrr_no) / max(mrr_no, 0.01) * 100
        direction2 = "↑ 提升" if delta_mrr > 0 else "↓ 下降" if delta_mrr < 0 else "→ 无变化"
        print(f"  MRR:       {mrr_no:.2%} → {mrr_re:.2%} ({direction2} {abs(delta_mrr):.1f}%)")

        latency_increase = sum(latencies_rerank) / len(latencies_rerank) - sum(latencies_no_rerank) / len(latencies_no_rerank)
        print(f"  延迟增加: {latency_increase:.0f}ms")

    # ── 整体评估 ──
    print(f"\n{'='*70}")
    print(f"综合评估")
    print(f"{'='*70}")

    p5 = agg_no_rerank["precision"][5]
    hit5 = agg_no_rerank["hit"][5]
    mrr = agg_no_rerank["mrr"]

    print(f"\n  核心指标 (无重排序):")
    print(f"    Precision@5:  {p5:.1%}")
    print(f"    Recall@5:     {agg_no_rerank['recall'][5]:.1%}")
    print(f"    Hit Rate@5:   {hit5:.0%}")
    print(f"    MRR:          {mrr:.1%}")
    print(f"    NDCG@5:       {agg_no_rerank['ndcg'][5]:.1%}")
    print(f"    平均延迟:     {sum(latencies_no_rerank)/len(latencies_no_rerank):.0f}ms")

    # 评级
    if p5 >= 0.8 and mrr >= 0.7:
        rating = "A — 优秀，检索精准度和排序质量都很高"
    elif p5 >= 0.6 and mrr >= 0.5:
        rating = "B — 良好，大部分查询能找到正确答案"
    elif p5 >= 0.4:
        rating = "C — 一般，有改善空间"
    else:
        rating = "D — 需要优化"

    print(f"\n  综合评级: {rating}")

    # ── 关键词 vs 语义对比 ──
    print(f"\n  检索来源分析:")
    all_sources = {"semantic": 0, "keyword": 0, "hybrid": 0}
    for results in all_results_no_rerank:
        for r in results:
            all_sources[r.source] = all_sources.get(r.source, 0) + 1
    total = sum(all_sources.values())
    for source, count in sorted(all_sources.items(), key=lambda x: x[1], reverse=True):
        pct = count / max(total, 1) * 100
        labels = {"semantic": "语义检索", "keyword": "关键词检索", "hybrid": "双源融合"}
        print(f"    {labels.get(source, source)}: {count} ({pct:.0f}%)")

    return {
        "agg_no_rerank": agg_no_rerank,
        "agg_rerank": agg_rerank,
        "per_query": all_metrics_no_rerank,
        "per_query_rerank": all_metrics_rerank if all_metrics_rerank else [],
        "latencies_no_rerank": latencies_no_rerank,
        "latencies_rerank": latencies_rerank,
        "query_cases": QUERY_CASES,
        "doc_ids": doc_ids,
    }


async def cleanup():
    """清理测试文档"""
    print("\n清理测试数据...")
    from models.database import async_session
    from sqlalchemy import select, delete
    from models.orm import Document
    from textdb.sqlite_fts import SQLiteFTS5
    from vectordb.factory import create_vectordb

    for doc_def in TEST_DOCS:
        import hashlib
        fh = hashlib.sha256(doc_def.content.encode()).hexdigest()
        async with async_session() as session:
            result = await session.execute(select(Document).where(Document.file_hash == fh))
            doc = result.scalar_one_or_none()
            if doc:
                vectordb = await create_vectordb()
                fts = SQLiteFTS5()
                await vectordb.delete_by_document(doc.id)
                await fts.delete_by_document(doc.id)
                await session.delete(doc)
                await session.commit()
                print(f"   已删除: {doc.filename}")
    print("清理完成")


if __name__ == "__main__":
    import asyncio as _asyncio
    try:
        results = _asyncio.run(run_evaluation())
    finally:
        _asyncio.run(cleanup())
