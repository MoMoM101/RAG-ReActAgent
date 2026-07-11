"""RAG 检索精准度评测脚本

评测维度：
- Precision@k / Recall@k: 精确率与召回率
- MRR (Mean Reciprocal Rank): 首个相关结果的平均倒数排名
- NDCG@k (Normalized Discounted Cumulative Gain): 位置加权相关性
- Hit Rate@k: 前k个结果中至少命中1个的比例
- Semantic vs Keyword 贡献分析
- 重排序影响分析
"""

import io
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure backend is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enum import Enum
from typing import Any

from config import settings


class AblationStrategy(Enum):
    SEMANTIC_ONLY = "semantic-only"
    KEYWORD_ONLY = "keyword-only"
    HYBRID_NO_RERANK = "hybrid-no-rerank"
    HYBRID_RERANK = "hybrid-rerank"


STRATEGY_LABELS = {
    AblationStrategy.SEMANTIC_ONLY: "仅语义搜索",
    AblationStrategy.KEYWORD_ONLY: "仅关键词搜索",
    AblationStrategy.HYBRID_NO_RERANK: "混合+RRF（无重排序）",
    AblationStrategy.HYBRID_RERANK: "混合+RRF+重排序",
}


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
    cross_doc_targets: dict[int, list[int]] | None = None  # {doc_idx: [chunk_indices]}


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
    TestDoc(
        filename="python_web_frameworks.txt",
        content=(
            "# Python Web 框架对比指南\n\n"
            "## Django\n\n"
            "Django 是一个\"电池自带\"的全栈 Web 框架，遵循 MTV（Model-Template-View）架构模式。\n"
            "内置功能包括 ORM、后台管理界面、认证系统、表单处理和 CSRF 保护。\n"
            "适合快速构建内容管理系统、电子商务平台等数据驱动的 Web 应用。\n"
            "Django REST Framework (DRF) 是其构建 RESTful API 的标准扩展。\n\n"
            "## Flask\n\n"
            "Flask 是一个轻量级微框架，核心只包含路由和请求/响应处理。\n"
            "开发者需要通过扩展来添加 ORM（如 SQLAlchemy）、表单验证（WTForms）等功能。\n"
            "Flask 的设计哲学是\"显式优于隐式\"，给予开发者最大自由度。\n"
            "适合小型 API 服务、微服务和原型开发。\n\n"
            "## FastAPI\n\n"
            "FastAPI 是新一代 Python Web 框架，基于 Starlette 和 Pydantic。\n"
            "核心特性：自动生成 OpenAPI 文档、类型提示驱动的参数验证、原生异步支持（async/await）。\n"
            "性能媲美 Node.js 和 Go，是目前增长最快的 Python 框架之一。\n"
            "特别适合构建高性能 API 和机器学习模型在线推理服务。\n\n"
            "## 选型建议\n\n"
            "大型全栈项目选择 Django，简单 API 服务选择 Flask，\n"
            "高性能异步 API 或 ML 模型部署选择 FastAPI。\n"
            "三者共享 Python 生态：均可用 pip 安装，支持 virtualenv/conda 环境管理。"
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
    # ═══ 短关键词 (3) ═══
    QueryCase(
        query="深度学习",
        relevant_chunk_indices=[2],
        doc_index=0,
        expected_keywords=["多层神经网络", "CNN", "Transformer", "BERT", "GPT"],
    ),
    QueryCase(
        query="Carbonara",
        relevant_chunk_indices=[0],
        doc_index=2,
        expected_keywords=["guanciale", "pecorino", "鸡蛋黄"],
    ),
    QueryCase(
        query="可再生能源",
        relevant_chunk_indices=[2],
        doc_index=1,
        expected_keywords=["太阳能", "风能", "20%", "光伏"],
    ),
    # ═══ 长描述句 (3) ═══
    QueryCase(
        query="我想要做一个基于神经网络的图像识别项目，需要用什么框架和工具来训练模型",
        relevant_chunk_indices=[2],
        doc_index=0,
        expected_keywords=["卷积神经网络", "CNN", "TensorFlow", "PyTorch"],
    ),
    QueryCase(
        query="如果我想在家里做一顿传统的地中海风味的完整晚餐，包括前菜和主菜，有什么推荐的菜谱和做法",
        relevant_chunk_indices=[0, 1, 2],
        doc_index=2,
        expected_keywords=["Carbonara", "希腊沙拉", "西班牙海鲜饭", "guanciale", "feta"],
    ),
    QueryCase(
        query="关于如何减缓全球变暖的趋势，国际社会和各國政府都采取了哪些重要措施和减排协议",
        relevant_chunk_indices=[3],
        doc_index=1,
        expected_keywords=["巴黎协定", "碳捕获", "CCS", "碳足迹", "2°C", "1.5°C"],
    ),
    # ═══ 精确匹配 (3) ═══
    QueryCase(
        query="ROC-AUC",
        relevant_chunk_indices=[3],
        doc_index=0,
        expected_keywords=["ROC-AUC", "准确率", "F1", "交叉验证"],
    ),
    QueryCase(
        query="Bomba 圆粒米",
        relevant_chunk_indices=[2],
        doc_index=2,
        expected_keywords=["Bomba", "saffron", "藏红花", "paellera"],
    ),
    QueryCase(
        query="paellera",
        relevant_chunk_indices=[2],
        doc_index=2,
        expected_keywords=["paellera", "socarrat", "西班牙海鲜饭"],
    ),
    # ═══ 跨文档 (2) ═══
    QueryCase(
        query="Python 有哪些常用的框架和开发库",
        relevant_chunk_indices=[0],
        doc_index=0,
        expected_keywords=["scikit-learn", "TensorFlow", "PyTorch", "Django", "Flask", "FastAPI"],
        cross_doc_targets={
            0: [0],
            3: [0, 1, 2],
        },
    ),
    QueryCase(
        query="如何用 Python 把一个训练好的机器学习模型部署为在线 API 服务",
        relevant_chunk_indices=[0],
        doc_index=0,
        expected_keywords=["TensorFlow", "PyTorch", "FastAPI", "async", "在线推理"],
        cross_doc_targets={
            0: [0],
            3: [2],
        },
    ),
    # ═══ 数值查询 (2) ═══
    QueryCase(
        query="420 ppm",
        relevant_chunk_indices=[1],
        doc_index=1,
        expected_keywords=["420", "ppm", "二氧化碳", "浓度"],
    ),
    QueryCase(
        query="1.1°C 温度上升",
        relevant_chunk_indices=[0],
        doc_index=1,
        expected_keywords=["1.1°C", "工业革命", "温度上升"],
    ),
    # ═══ 同义改写 (2) ═══
    QueryCase(
        query="深度神经网络的常见架构和训练方法",
        relevant_chunk_indices=[2],
        doc_index=0,
        expected_keywords=["多层神经网络", "CNN", "RNN", "Transformer", "BERT", "GPT"],
    ),
    QueryCase(
        query="意大利培根蛋奶面的正宗做法是什么",
        relevant_chunk_indices=[0],
        doc_index=2,
        expected_keywords=["guanciale", "pecorino", "鸡蛋黄", "Carbonara"],
    ),
    # ═══ 中英混合 (2) ═══
    QueryCase(
        query="Python TensorFlow 训练用什么 GPU 加速",
        relevant_chunk_indices=[2],
        doc_index=0,
        expected_keywords=["GPU", "加速训练", "TensorFlow", "深度学习"],
    ),
    QueryCase(
        query="FastAPI 和 Django 有什么不同，各自适合什么场景",
        relevant_chunk_indices=[0, 2, 3],
        doc_index=3,
        expected_keywords=["Django", "FastAPI", "ORM", "异步", "选型"],
    ),
    # ═══ 否定/排除 (2) ═══
    QueryCase(
        query="不用奶油的意面做法有哪些",
        relevant_chunk_indices=[0],
        doc_index=2,
        expected_keywords=["切忌使用奶油", "鸡蛋黄", "guanciale", "pecorino"],
    ),
    QueryCase(
        query="不涉及深度学习的传统机器学习方法有哪些",
        relevant_chunk_indices=[0, 1],
        doc_index=0,
        expected_keywords=["scikit-learn", "分类", "回归", "聚类", "缺失值", "归一化"],
    ),
    # ═══ 宽泛/模糊 (2) ═══
    QueryCase(
        query="怎么做",
        relevant_chunk_indices=[0, 1, 2],
        doc_index=2,
        expected_keywords=["Carbonara", "希腊沙拉", "西班牙海鲜饭"],
    ),
    QueryCase(
        query="有哪些方法",
        relevant_chunk_indices=[0, 1],
        doc_index=0,
        expected_keywords=["缺失值处理", "归一化", "编码", "分类", "回归", "聚类"],
    ),
]


# ── 评测指标计算 ──────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Tokenize text for Jaccard similarity comparison.

    Uses jieba for Chinese word segmentation if installed,
    otherwise falls back to whitespace-based split().
    """
    try:
        import jieba
        tokens = [t.strip() for t in jieba.cut(text)]
        return [t for t in tokens if t]
    except ImportError:
        return text.lower().split()


def precision_at_k(relevant: set[int], retrieved_count: int, k: int) -> float:
    """Precision@k: top-k中相关结果占比"""
    # relevant 是chunk级别的索引集合，无法直接映射到retrieved排名
    # 使用标签：每个retrieved结果标记是否相关
    return 0.0  # 占位，实际在主流程中计算


def compute_metrics(
    retrieved_chunks: list[str],  # 检索到的文本列表
    ground_truth_texts: list[str],  # 标注相关的文本列表
    k_values: tuple[int, ...] = (3, 5, 10),
) -> dict:
    """[LEGACY] 计算标准 IR 评测指标 — 使用 Jaccard 文本匹配。

    已废弃：此函数使用字面 Jaccard 相似度判定相关性，可能导致 Recall > 100%。
    新代码请使用 ``eval_metrics.compute_metrics_v2()``，基于稳定 document_key/section_key。
    该函数仅保留用于历史结果复现，不作为正式报告入口。
    """
    # 相关性判定：用 Jaccard 相似度（word-level）检测检索文本是否命中标注
    def is_relevant(text: str, gt_texts: list[str]) -> bool:
        if not text.strip():
            return False
        text_words = set(_tokenize(text))
        for gt in gt_texts:
            gt_words = set(_tokenize(gt))
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
    results: dict[str, Any] = {"precision": {}, "recall": {}, "mrr": 0.0, "ndcg": {}, "hit": {}}
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


def _classify_query(qc: QueryCase) -> str:
    """Heuristic query category classifier for reporting."""
    q = qc.query
    if qc.cross_doc_targets:
        return "cross-document"
    if len(q) <= 6:
        return "short-keyword"
    if len(q) >= 25:
        return "long-descriptive"
    if any(kw in q for kw in ["不用", "不涉及", "无", "非"]):
        return "negation-contrast"
    if len(q) <= 10:
        return "ambiguous-broad"
    return "natural-question"


def save_results(results: dict, filepath: str = "evaluation_results.json") -> str:
    """Persist evaluation results as JSON for cross-run comparison."""
    from datetime import datetime

    per_query_summary = []
    for j, qc in enumerate(results.get("query_cases", [])):
        metrics = results.get("per_query", [])
        m = metrics[j] if j < len(metrics) else {}
        per_query_summary.append({
            "query": qc.query,
            "category": _classify_query(qc),
            "mrr": m.get("mrr", 0),
            "precision_k3": m.get("precision", {}).get(3, 0),
            "precision_k5": m.get("precision", {}).get(5, 0),
            "hit_k5": m.get("hit", {}).get(5, 0),
        })

    payload = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "rerank_enabled": settings.rerank_enabled,
        },
        "num_queries": len(results.get("query_cases", [])),
        "aggregate_no_rerank": {
            k: v for k, v in results.get("agg_no_rerank", {}).items()
            if k != "per_query"
        },
        "aggregate_rerank": results.get("agg_rerank"),
        "avg_latency_ms_no_rerank": (
            sum(results.get("latencies_no_rerank", []))
            / max(len(results.get("latencies_no_rerank", [])), 1)
        ),
        "per_query": per_query_summary,
    }

    # Add ablation data
    if results.get("strategy_results"):
        sr = results["strategy_results"]
        payload["ablation"] = {}
        for strat_name, strat_data in sr.items():
            strat_key = strat_name.value if hasattr(strat_name, 'value') else str(strat_name)
            metrics_list = strat_data["metrics"]
            n = max(len(metrics_list), 1)
            p5 = sum(m["precision"].get(5, 0) for m in metrics_list) / n
            mrr_val = sum(m.get("mrr", 0) for m in metrics_list) / n
            h5 = sum(m["hit"].get(5, 0) for m in metrics_list) / n
            n5 = sum(m["ndcg"].get(5, 0) for m in metrics_list) / n
            lat = sum(strat_data["latencies"]) / max(len(strat_data["latencies"]), 1)
            payload["ablation"][strat_key] = {
                "precision_k5": p5,
                "mrr": mrr_val,
                "hit_k5": h5,
                "ndcg_k5": n5,
                "avg_latency_ms": lat,
            }

    outpath = Path(__file__).resolve().parent / filepath
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return str(outpath)


# ── 单策略执行 ──────────────────────────────────────────────────

async def run_single_strategy(
    strategy: AblationStrategy,
    query: str,
    top_k: int = 10,
    document_id: str = "",
) -> tuple[list, int]:
    """Run a single retrieval strategy and return (results, latency_ms)."""
    import time as _time

    from embedding.factory import create_embedding
    from rag.retriever import RetrievalResult, hybrid_search
    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    t0 = _time.time()

    if strategy == AblationStrategy.KEYWORD_ONLY:
        fts = BM25Search()
        text_results = await fts.search(query, top_k=top_k, document_id=document_id)
        results = [
            RetrievalResult(
                chunk_id=r.chunk_id, document_id=r.document_id,
                text=r.text, score=r.score, source="keyword"
            )
            for r in text_results
        ]

    elif strategy == AblationStrategy.SEMANTIC_ONLY:
        embedding = create_embedding()
        vectordb = await create_vectordb()
        query_vector = await embedding.embed_query(query)
        vector_results = await vectordb.search(query_vector, top_k=top_k)
        results = [
            RetrievalResult(
                chunk_id=r.chunk_id, document_id=r.document_id,
                text=r.text, score=r.score, source="semantic"
            )
            for r in vector_results
        ]

    elif strategy == AblationStrategy.HYBRID_NO_RERANK:
        results = list(await hybrid_search(query, top_k=top_k, use_rerank=False))

    elif strategy == AblationStrategy.HYBRID_RERANK:
        results = list(await hybrid_search(query, top_k=top_k, use_rerank=True))

    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    latency = int((_time.time() - t0) * 1000)
    return results, latency


# ── 主评测流程 ─────────────────────────────────────────────────

async def run_evaluation():
    print("=" * 70)
    print("RAG 检索精准度评测")
    print(f"Embedding: {settings.embedding_provider}/{settings.embedding_model}")
    print(f"Reranker: {'enabled' if settings.rerank_enabled else 'disabled'}")
    print(f"Chunk size: {settings.chunk_size}, Overlap: {settings.chunk_overlap}")
    print(f"Qdrant: {'local' if not settings.qdrant_host else settings.qdrant_host}")
    print("=" * 70)

    from sqlalchemy import select

    from models.database import async_session
    from models.orm import Document
    from rag.pipeline import ingest_document
    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    # ── Step 1: 清理旧数据 ──
    print("\n[1/5] 清理旧测试数据...")
    async with async_session() as session:
        # 清理所有匹配测试文档的历史数据（按文件名 + 按 hash 双重匹配）
        test_filenames = [d.filename for d in TEST_DOCS]
        result = await session.execute(
            select(Document).where(Document.filename.in_(test_filenames))
        )
        existing_docs = result.scalars().all()
        for existing in existing_docs:
            vectordb = await create_vectordb()
            fts = BM25Search()
            await vectordb.delete_by_document(existing.id)
            await fts.delete_by_document(existing.id)
            await session.delete(existing)
        await session.commit()
        if existing_docs:
            print(f"   已清理 {len(existing_docs)} 份旧测试文档")
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
        except ValueError:
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
        # 确定来源：跨文档标注或单文档
        sources = qc.cross_doc_targets if qc.cross_doc_targets else {qc.doc_index: qc.relevant_chunk_indices}
        for doc_idx, chunk_indices in sources.items():
            if doc_idx >= len(doc_chunks):
                continue
            avail_chunks = doc_chunks[doc_idx]
            n_avail = len(avail_chunks)
            for chunk_idx in chunk_indices:
                # 如果原始 chunk 索引超出范围（chunk 被合并），映射到最近的可用 chunk
                mapped_idx = min(chunk_idx, n_avail - 1) if n_avail > 0 else chunk_idx
                if mapped_idx < n_avail and avail_chunks[mapped_idx] not in texts:
                    texts.append(avail_chunks[mapped_idx])
        ground_truth_texts[q_idx] = texts

    # ── Step 4: 消融对比评测 ──
    print("\n[4/5] 运行消融对比评测...")
    k_values = [3, 5, 10]

    strategies = [
        AblationStrategy.SEMANTIC_ONLY,
        AblationStrategy.KEYWORD_ONLY,
        AblationStrategy.HYBRID_NO_RERANK,
    ]
    if settings.rerank_enabled:
        strategies.append(AblationStrategy.HYBRID_RERANK)

    strategy_results: dict = {
        s: {"metrics": [], "latencies": [], "results": []}
        for s in strategies
    }

    for j, qc in enumerate(QUERY_CASES):
        gt = ground_truth_texts[j]

        for strategy in strategies:
            results, lat = await run_single_strategy(strategy, qc.query, top_k=10)
            strategy_results[strategy]["results"].append(results)
            strategy_results[strategy]["latencies"].append(lat)

            retrieved_texts = [r.text for r in results]
            metrics = compute_metrics(retrieved_texts, gt, k_values)
            strategy_results[strategy]["metrics"].append(metrics)

        mrr = strategy_results[AblationStrategy.HYBRID_NO_RERANK]["metrics"][-1]["mrr"]
        print(f"   [{j+1:2d}/{len(QUERY_CASES)}] \"{qc.query[:40]}\" → MRR={mrr:.2f}")

    # ── Step 5: 消融对比汇总 ──
    print("\n[5/5] 消融对比汇总...\n")
    print("=" * 70)
    print("消融对比：各检索策略贡献分析")
    print("=" * 70)

    # Aggregation helper (kept from original for compatibility)
    def aggregate(metrics_list: list[dict[str, Any]], k_vals: list[int]) -> dict[str, Any]:
        n = len(metrics_list)
        agg: dict[str, Any] = {"precision": {}, "recall": {}, "ndcg": {}, "hit": {}, "mrr": 0.0}
        for k in k_vals:
            agg["precision"][k] = sum(m["precision"][k] for m in metrics_list) / n
            agg["recall"][k] = sum(m["recall"][k] for m in metrics_list) / n
            agg["ndcg"][k] = sum(m["ndcg"][k] for m in metrics_list) / n
            agg["hit"][k] = sum(m["hit"][k] for m in metrics_list) / n
        agg["mrr"] = sum(m["mrr"] for m in metrics_list) / n
        return agg

    print("\n{:<24} {:>7} {:>7} {:>7} {:>7} {:>7} {:>7}".format("策略", "P@5", "Recall@5", "MRR", "Hit@5", "NDCG@5", "延迟ms"))
    print("-" * 78)

    def _agg(metrics_list):
        return aggregate(metrics_list, k_values)

    for strategy in strategies:
        agg_m = _agg(strategy_results[strategy]["metrics"])
        avg_lat = (sum(strategy_results[strategy]["latencies"])
                   / max(len(strategy_results[strategy]["latencies"]), 1))
        label = STRATEGY_LABELS.get(strategy, strategy.value)
        print(f"{label:<24} {agg_m['precision'][5]:>6.1%} {agg_m['recall'][5]:>7.1%} "
              f"{agg_m['mrr']:>6.1%} "
              f"{agg_m['hit'][5]:>6.0%} {agg_m['ndcg'][5]:>6.1%} {avg_lat:>6.0f}")

    # 来源分析
    hybrid_key = AblationStrategy.HYBRID_NO_RERANK
    print(f"\n  检索来源分析 ({STRATEGY_LABELS[hybrid_key]}):")
    all_sources: dict[str, int] = {}
    for results in strategy_results[hybrid_key]["results"]:
        for r in results:
            all_sources[r.source] = all_sources.get(r.source, 0) + 1
    total = sum(all_sources.values())
    labels_src = {"semantic": "语义检索", "keyword": "关键词检索", "hybrid": "双源融合"}
    for source, count in sorted(all_sources.items(), key=lambda x: x[1], reverse=True):
        pct = count / max(total, 1) * 100
        print(f"    {labels_src.get(source, source)}: {count} ({pct:.0f}%)")

    # 综合评级
    agg = _agg(strategy_results[AblationStrategy.HYBRID_NO_RERANK]["metrics"])
    p5 = agg["precision"][5]
    mrr = agg["mrr"]
    hit5 = agg["hit"][5]
    if p5 >= 0.8 and mrr >= 0.7:
        rating = "A — 优秀"
    elif p5 >= 0.6 and mrr >= 0.5:
        rating = "B — 良好"
    elif p5 >= 0.4:
        rating = "C — 一般"
    else:
        rating = "D — 需要优化"

    avg_latency_ms = sum(strategy_results[hybrid_key]["latencies"]) / len(strategy_results[hybrid_key]["latencies"])
    print("\n  核心指标 (无重排序):")
    print("    Precision@5: {:.1%}  Recall@5: {:.1%}  Hit@5: {:.0%}  MRR: {:.1%}  NDCG@5: {:.1%}".format(
        p5, agg["recall"][5], hit5, mrr, agg["ndcg"][5]))
    print(f"    平均延迟: {avg_latency_ms:.0f}ms")
    print(f"  综合评级: {rating}")

    # Compute aggregated metrics for backward compatibility
    hybrid_no_rerank = strategy_results[AblationStrategy.HYBRID_NO_RERANK]
    agg_no_rerank = _agg(hybrid_no_rerank["metrics"])
    agg_rerank = None
    latencies_no_rerank = hybrid_no_rerank["latencies"]
    latencies_rerank = []
    all_metrics_no_rerank = hybrid_no_rerank["metrics"]
    all_metrics_rerank = []

    rerank_strat = strategy_results.get(AblationStrategy.HYBRID_RERANK)
    if rerank_strat:
        agg_rerank = _agg(rerank_strat["metrics"])
        latencies_rerank = rerank_strat["latencies"]
        all_metrics_rerank = rerank_strat["metrics"]

    results = {
        "agg_no_rerank": agg_no_rerank,
        "agg_rerank": agg_rerank,
        "per_query": all_metrics_no_rerank,
        "per_query_rerank": all_metrics_rerank,
        "latencies_no_rerank": latencies_no_rerank,
        "latencies_rerank": latencies_rerank,
        "query_cases": QUERY_CASES,
        "doc_ids": doc_ids,
        "strategy_results": strategy_results,
    }

    # ── Persist results ──
    results_file = save_results(results)
    print(f"\n  评测结果已保存至: {results_file}")
    return results


async def cleanup():
    """清理测试文档"""
    print("\n清理测试数据...")
    from sqlalchemy import select

    from models.database import async_session
    from models.orm import Document
    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    test_filenames = [d.filename for d in TEST_DOCS]
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.filename.in_(test_filenames))
        )
        docs = result.scalars().all()
        for doc in docs:
            vectordb = await create_vectordb()
            fts = BM25Search()
            await vectordb.delete_by_document(doc.id)
            await fts.delete_by_document(doc.id)
            await session.delete(doc)
        await session.commit()
        if docs:
            print(f"   已清理 {len(docs)} 份测试文档")
    print("清理完成")


def _print_comparison(current: dict, previous: dict) -> None:
    """Print side-by-side comparison with previous evaluation run."""
    print("\n" + "=" * 70)
    print("与上次评测对比")
    print("=" * 70)

    cur_agg = current.get("agg_no_rerank", {})
    prev_agg = previous.get("aggregate_no_rerank", {})

    if not prev_agg:
        print("  上次评测无数据，无法对比")
        return

    print(f"\n{'指标':<20} {'上次':>10} {'本次':>10} {'变化':>10}")
    print("-" * 54)

    for k_val in (3, 5, 10):
        cur_p = cur_agg.get("precision", {}).get(k_val, 0)
        prev_p = prev_agg.get("precision", {}).get(str(k_val), 0)
        if prev_p == 0:
            prev_p = prev_agg.get("precision", {}).get(k_val, 0)
        delta = (cur_p - prev_p) / max(prev_p, 0.01) * 100 if prev_p else 0
        print(f"Precision@{k_val:<14} {prev_p:>9.1%} {cur_p:>9.1%} {delta:>+9.0f}%")

    for key, label in [("mrr", "MRR")]:
        cur_val = cur_agg.get(key, 0)
        prev_val = prev_agg.get(key, 0)
        delta_pct = (cur_val - prev_val) / max(prev_val, 0.01) * 100 if prev_val else 0
        print(f"{label:<20} {prev_val:>9.1%} {cur_val:>9.1%} {delta_pct:>+9.0f}%")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RAG Retrieval Evaluation")
    parser.add_argument("--compare", type=str, default=None,
                        help="Path to previous evaluation JSON for comparison")
    parser.add_argument("--output", type=str, default="evaluation_results.json",
                        help="Output JSON file path")
    args = parser.parse_args()

    import asyncio as _asyncio

    try:
        results = _asyncio.run(run_evaluation())

        output_path = save_results(results, args.output)
        print(f"\n结果已保存至: {output_path}")

        if args.compare:
            prev_path = Path(args.compare)
            if prev_path.exists():
                prev_data = json.loads(prev_path.read_text(encoding="utf-8"))
                _print_comparison(results, prev_data)
            else:
                print(f"\n[WARN] 对比文件不存在: {args.compare}")
    finally:
        _asyncio.run(cleanup())
