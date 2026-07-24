"""Parameter sweep for hybrid search optimization."""
import argparse
import contextlib
import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA_DIR = Path(__file__).resolve().parent / "eval_data"
_DOC_FILES = [
    "paygate_api_v3.txt", "mcu_catalog_2024q3.txt",
    "drug_reference_cardio.txt", "data_compliance_checklist.txt",
]


def load_business_data():
    from evaluate_rag import TestDoc
    docs = []
    for fname in _DOC_FILES:
        content = (_DATA_DIR / fname).read_text(encoding="utf-8")
        docs.append(TestDoc(filename=fname, content=content))
    return docs, make_queries()


def make_queries():
    from evaluate_rag import QueryCase as QC  # noqa: N817
    Q = QC  # noqa: N806
    return [
        Q(query="ERR_40003", relevant_chunk_indices=[0], doc_index=0, expected_keywords=["ERR_40003"]),
        Q(query="ERR_40401", relevant_chunk_indices=[1], doc_index=0, expected_keywords=["ERR_40401"]),
        Q(query="ERR_40201", relevant_chunk_indices=[2], doc_index=0, expected_keywords=["ERR_40201"]),
        Q(query="STM32H743VI", relevant_chunk_indices=[0], doc_index=1, expected_keywords=["STM32H743VI"]),
        Q(query="R7FA6M5BH3CFC", relevant_chunk_indices=[2], doc_index=1, expected_keywords=["R7FA6M5BH3CFC"]),
        Q(query="ESP32-S3R8 price and stock", relevant_chunk_indices=[3], doc_index=1, expected_keywords=["ESP32"]),
        Q(query="Clopidogrel loading dose", relevant_chunk_indices=[1], doc_index=2, expected_keywords=["Clopidogrel"]),
        Q(query="Nifedipine GITS contraindications", relevant_chunk_indices=[2], doc_index=2, expected_keywords=["Nifedipine"]),
        Q(query="Atorvastatin drug interactions", relevant_chunk_indices=[0], doc_index=2, expected_keywords=["Atorvastatin"]),
        Q(query="clause 32 breach report deadline",
          relevant_chunk_indices=[2], doc_index=3, expected_keywords=["32", "CAC"]),
        Q(query="clause 26 database password rotation",
          relevant_chunk_indices=[1], doc_index=3, expected_keywords=["26", "90 days"]),
        Q(query="clause 18 personal information export",
          relevant_chunk_indices=[0], doc_index=3, expected_keywords=["18", "export"]),
        Q(query="ADC 5MSPS microcontroller",
          relevant_chunk_indices=[0], doc_index=1, expected_keywords=["5MSPS"]),
        Q(query="-40C to 125C industrial MCU",
          relevant_chunk_indices=[1], doc_index=1, expected_keywords=["-40", "125"]),
        Q(query="80mg max dose atorvastatin",
          relevant_chunk_indices=[0], doc_index=2, expected_keywords=["80mg"]),
        Q(query="HMG-CoA reductase inhibitor",
          relevant_chunk_indices=[0], doc_index=2, expected_keywords=["HMG-CoA"]),
        Q(query="P2Y12 receptor antagonist",
          relevant_chunk_indices=[1], doc_index=2, expected_keywords=["P2Y12"]),
        Q(query="what parameters are required to create a payment order",
          relevant_chunk_indices=[0], doc_index=0, expected_keywords=["amount"]),
        Q(query="which chips support CAN FD bus interface",
          relevant_chunk_indices=[0, 2], doc_index=1, expected_keywords=["CAN FD"]),
        Q(query="how soon must users be notified after a data breach",
          relevant_chunk_indices=[2], doc_index=3, expected_keywords=["72 hours"]),
        Q(query="STM32 MCU with ETH ethernet interface",
          relevant_chunk_indices=[0], doc_index=1, expected_keywords=["STM32"]),
        Q(query="refund refund_amount parameter limit",
          relevant_chunk_indices=[2], doc_index=0, expected_keywords=["refund_amount"]),
        Q(query="where is AES-256 encryption mentioned",
          relevant_chunk_indices=[2], doc_index=1, expected_keywords=["AES-256"],
          cross_doc_targets={1: [2], 3: [1]}),
        Q(query="encryption", relevant_chunk_indices=[2], doc_index=3,
          cross_doc_targets={3: [1, 2], 1: [2]}),
        Q(query="security", relevant_chunk_indices=[0], doc_index=3,
          cross_doc_targets={3: [0, 1, 2], 1: [2]}),
    ]


def apply_settings(params):
    from config import settings
    if "rrf_k" in params:
        settings.rrf_k = params["rrf_k"]
    if "rerank_top_n" in params:
        settings.rerank_top_n = params["rerank_top_n"]
    if "chunk_quality_filter_enabled" in params:
        settings.chunk_quality_filter_enabled = params["chunk_quality_filter_enabled"]
    if "dedup_enabled" in params:
        settings.dedup_enabled = params["dedup_enabled"]
    if "retrieval_top_k" in params:
        settings.retrieval_top_k = params["retrieval_top_k"]
    if "rrf_semantic_weight" in params:
        settings.rrf_semantic_weight = params["rrf_semantic_weight"]
    if "rrf_keyword_weight" in params:
        settings.rrf_keyword_weight = params["rrf_keyword_weight"]
    if "query_rewrite_enabled" in params:
        settings.query_rewrite_enabled = params["query_rewrite_enabled"]
    if "rrf_adaptive_enabled" in params:
        settings.rrf_adaptive_enabled = params["rrf_adaptive_enabled"]


async def run_one_eval(docs, queries):
    import evaluate_rag
    evaluate_rag.TEST_DOCS = docs
    evaluate_rag.QUERY_CASES = queries
    try:
        results = await evaluate_rag.run_evaluation()
        agg = results["strategy_results"]
        from evaluate_rag import AblationStrategy
        hk = AblationStrategy.HYBRID_RERANK
        sk = AblationStrategy.SEMANTIC_ONLY
        ml = agg[hk]["metrics"]
        sl = agg[sk]["metrics"]
        n = max(len(ml), 1)
        return {
            "p5_h": sum(m["precision"][5] for m in ml) / n,
            "p5_s": sum(m["precision"][5] for m in sl) / n,
            "r5_h": sum(m["recall"][5] for m in ml) / n,
            "mrr_h": sum(m["mrr"] for m in ml) / n,
            "hit5_h": sum(m["hit"][5] for m in ml) / n,
            "ndcg5_h": sum(m["ndcg"][5] for m in ml) / n,
            "lat_h": sum(agg[hk]["latencies"]) / max(len(agg[hk]["latencies"]), 1),
        }
    except Exception as e:
        print(f"\n[ERROR] {e}")
        return None
    finally:
        with contextlib.suppress(Exception):
            await evaluate_rag.cleanup()


async def sweep(grid, docs, queries):
    keys = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    total = len(combos)
    print(f"Combos: {total}, est. ~{total * 25 * 0.35 / 60:.0f} min\n")

    results = []
    t0 = time.time()
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo, strict=False))
        apply_settings(params)
        r = await run_one_eval(docs, queries)
        if r is None:
            print(f"[{i+1:3d}/{total}] FAILED, skipping")
            continue
        r["params"] = params
        results.append(r)
        elapsed = time.time() - t0
        eta = (elapsed / (i + 1)) * (total - i - 1) if i > 0 else 0
        p = params
        parts = [f"[{i+1:3d}/{total}]"]
        if "rrf_k" in p:
            parts.append(f"rrf_k={p['rrf_k']:3d}")
        if "rerank_top_n" in p:
            parts.append(f"rn={p['rerank_top_n']:2d}")
        if "retrieval_top_k" in p:
            parts.append(f"top_k={p['retrieval_top_k']:2d}")
        if "rrf_semantic_weight" in p:
            parts.append(f"sem_w={p['rrf_semantic_weight']:.1f}")
        if "rrf_keyword_weight" in p:
            parts.append(f"kw_w={p['rrf_keyword_weight']:.1f}")
        if "query_rewrite_enabled" in p:
            parts.append(f"rewr={str(p['query_rewrite_enabled']):5s}")
        if "rrf_adaptive_enabled" in p:
            parts.append(f"adap={str(p['rrf_adaptive_enabled']):5s}")
        if "chunk_quality_filter_enabled" in p:
            parts.append(f"qual={str(p['chunk_quality_filter_enabled']):5s}")
        if "dedup_enabled" in p:
            parts.append(f"dedup={str(p['dedup_enabled']):5s}")
        parts.append(
            f"P@5={r['p5_h']:.1%} sem={r['p5_s']:.1%} "
            f"l={r['lat_h']:.0f}ms ETA={eta / 60:.0f}m"
        )
        print(" ".join(parts))

    results.sort(key=lambda x: x["p5_h"], reverse=True)
    return results


def param_keys(results):
    """Detect which param keys are present in results."""
    if not results:
        return []
    return [k for k in results[0]["params"]]


def print_table(results):
    if not results:
        print("\nNo results.")
        return
    keys = param_keys(results)
    header = "Rank  " + "  ".join(f"{k:<8}" for k in keys) + "  P@5(h)    P@5(s)   Rec@5   MRR      Hit@5  NDCG@5   Lat"
    sep = "-" * (len(header) + 10)
    print("\n" + sep)
    print(header)
    print("-" * len(sep))
    for i, r in enumerate(results[:20]):
        p = r["params"]
        vals = "  ".join(f"{str(p[k]):<8}" for k in keys)
        print(f"{i+1:<5}  {vals}  {r['p5_h']:<.1%}     {r['p5_s']:<.1%}    "
              f"{r['r5_h']:<.1%}   {r['mrr_h']:<.4f}  {r['hit5_h']:<.0%}    "
              f"{r['ndcg5_h']:<.4f}   {r['lat_h']:<.0f}")
    best = results[0]
    d = best["p5_h"] - best["p5_s"]
    s = "+" if d >= 0 else ""
    print(f"\nBest hybrid-rerank P@5 vs semantic-only: {s}{d:.1%}")
    print(f"Best config: {best['params']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--rrf-only", action="store_true")
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if args.expanded:
        # Phase 2: expand new-framework params
        # Baseline (fixed): rrf_k=30, rerank_top_n=24, quality=True, dedup=False
        from config import settings
        settings.rrf_k = 30
        settings.rerank_top_n = 24
        settings.chunk_quality_filter_enabled = True
        settings.dedup_enabled = False
        grid = {
            "retrieval_top_k": [4, 6, 8, 12],
            "rrf_semantic_weight": [0.5, 1.0, 1.5, 2.0, 3.0],
            "query_rewrite_enabled": [True, False],
        }
    elif args.quick:
        grid = {
            "rrf_k": [30, 60, 120],
            "rerank_top_n": [16, 24],
            "chunk_quality_filter_enabled": [True, False],
            "dedup_enabled": [True, False],
        }
    elif args.rrf_only:
        grid = {
            "rrf_k": [10, 20, 30, 45, 60, 90, 120, 180, 240],
            "rerank_top_n": [16],
            "chunk_quality_filter_enabled": [True],
            "dedup_enabled": [True],
        }
    else:
        grid = {
            "rrf_k": [30, 60, 120],
            "rerank_top_n": [16, 24, 32],
            "chunk_quality_filter_enabled": [True, False],
            "dedup_enabled": [True, False],
        }

    docs, queries = load_business_data()
    import asyncio
    results = asyncio.run(sweep(grid, docs, queries))
    print_table(results)

    if args.output:
        sr = []
        for r in results:
            d = dict(r)
            d["params"] = dict(d["params"])
            sr.append(d)
        Path(args.output).write_text(json.dumps(sr, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
