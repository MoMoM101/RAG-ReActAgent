"""Runner: monkey-patch evaluate_rag with business docs, then run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_DIR = Path(__file__).resolve().parent / "eval_data"

# ---- load docs from text files ----
def _make_testdoc(filename):
    from evaluate_rag import TestDoc
    content = (DATA_DIR / filename).read_text(encoding="utf-8")
    return TestDoc(filename=filename, content=content, file_type=".txt")

# ---- define query cases ----
def _make_queries():
    from evaluate_rag import QueryCase

    return [
        # exact-code (3)
        QueryCase(query="ERR_40003", relevant_chunk_indices=[0], doc_index=0,
                  expected_keywords=["ERR_40003", "amount", "1 million"]),
        QueryCase(query="ERR_40401", relevant_chunk_indices=[1], doc_index=0,
                  expected_keywords=["ERR_40401", "order not found"]),
        QueryCase(query="ERR_40201", relevant_chunk_indices=[2], doc_index=0,
                  expected_keywords=["ERR_40201", "balance", "insufficient"]),
        # sku-lookup (3)
        QueryCase(query="STM32H743VI", relevant_chunk_indices=[0], doc_index=1,
                  expected_keywords=["STM32H743VI", "Cortex-M7", "480MHz"]),
        QueryCase(query="R7FA6M5BH3CFC", relevant_chunk_indices=[2], doc_index=1,
                  expected_keywords=["R7FA6M5BH3CFC", "Cortex-M33", "TrustZone"]),
        QueryCase(query="ESP32-S3R8 price and stock", relevant_chunk_indices=[3], doc_index=1,
                  expected_keywords=["ESP32-S3R8", "18.50", "890,000"]),
        # drug-lookup (3)
        QueryCase(query="Clopidogrel loading dose", relevant_chunk_indices=[1], doc_index=2,
                  expected_keywords=["Clopidogrel", "300mg", "loading", "P2Y12"]),
        QueryCase(query="Nifedipine GITS contraindications", relevant_chunk_indices=[2], doc_index=2,
                  expected_keywords=["Nifedipine", "cardiogenic shock", "aortic stenosis"]),
        QueryCase(query="Atorvastatin drug interactions", relevant_chunk_indices=[0], doc_index=2,
                  expected_keywords=["Atorvastatin", "CYP3A4", "clarithromycin"]),
        # clause-lookup (3)
        QueryCase(query="clause 32 breach report deadline", relevant_chunk_indices=[2], doc_index=3,
                  expected_keywords=["32", "100K", "8 hours", "CAC"]),
        QueryCase(query="clause 26 database password rotation", relevant_chunk_indices=[1], doc_index=3,
                  expected_keywords=["26", "16 characters", "90 days", "rotation"]),
        QueryCase(query="clause 18 personal information export", relevant_chunk_indices=[0], doc_index=3,
                  expected_keywords=["18", "export", "1 million", "CAC approval"]),
        # numeric-spec (3)
        QueryCase(query="ADC 5MSPS microcontroller", relevant_chunk_indices=[0], doc_index=1,
                  expected_keywords=["STM32H743VI", "5MSPS", "ADC", "12-bit"]),
        QueryCase(query="-40C to 125C industrial MCU", relevant_chunk_indices=[1], doc_index=1,
                  expected_keywords=["STM32G474RE", "-40", "125", "HRTIM"]),
        QueryCase(query="80mg max dose atorvastatin", relevant_chunk_indices=[0], doc_index=2,
                  expected_keywords=["atorvastatin", "80mg", "max", "HMG-CoA"]),
        # jargon (2)
        QueryCase(query="HMG-CoA reductase inhibitor", relevant_chunk_indices=[0], doc_index=2,
                  expected_keywords=["Atorvastatin", "HMG-CoA", "Lipitor", "statin"]),
        QueryCase(query="P2Y12 receptor antagonist", relevant_chunk_indices=[1], doc_index=2,
                  expected_keywords=["Clopidogrel", "P2Y12", "Plavix", "antiplatelet"]),
        # semantic-nl (3)
        QueryCase(query="what parameters are required to create a payment order",
                  relevant_chunk_indices=[0], doc_index=0,
                  expected_keywords=["amount", "currency", "out_trade_no", "POST /orders"]),
        QueryCase(query="which chips support CAN FD bus interface",
                  relevant_chunk_indices=[0, 2], doc_index=1,
                  expected_keywords=["STM32H743VI", "R7FA6M5BH3CFC", "CAN FD"]),
        QueryCase(query="how soon must users be notified after a data breach",
                  relevant_chunk_indices=[2], doc_index=3,
                  expected_keywords=["72 hours", "clause 34", "notify"]),
        # mixed-lang (2)
        QueryCase(query="STM32 MCU with ETH ethernet interface",
                  relevant_chunk_indices=[0], doc_index=1,
                  expected_keywords=["STM32H743VI", "ETH", "Ethernet"]),
        QueryCase(query="refund refund_amount parameter limit",
                  relevant_chunk_indices=[2], doc_index=0,
                  expected_keywords=["refund_amount", "original", "ERR_40004"]),
        # cross-doc (1)
        QueryCase(query="where is AES-256 encryption mentioned",
                  relevant_chunk_indices=[2], doc_index=1,
                  expected_keywords=["AES-256", "R7FA6M5BH3CFC", "AES-256-GCM"],
                  cross_doc_targets={1: [2], 3: [1]}),
        # vague (2)
        QueryCase(query="encryption", relevant_chunk_indices=[2], doc_index=3,
                  cross_doc_targets={3: [1, 2], 1: [2]}),
        QueryCase(query="security", relevant_chunk_indices=[0], doc_index=3,
                  cross_doc_targets={3: [0, 1, 2], 1: [2]}),
    ]


if __name__ == "__main__":
    import asyncio

    import evaluate_rag

    # Monkey-patch the module-level data
    doc_files = [
        "paygate_api_v3.txt",
        "mcu_catalog_2024q3.txt",
        "drug_reference_cardio.txt",
        "data_compliance_checklist.txt",
    ]
    evaluate_rag.TEST_DOCS = [_make_testdoc(f) for f in doc_files]
    evaluate_rag.QUERY_CASES = _make_queries()

    try:
        results = asyncio.run(evaluate_rag.run_evaluation())
        output_path = evaluate_rag.save_results(results, "evaluation_results_business_v2.json")
        print(f"\nResults saved to: {output_path}")
    finally:
        asyncio.run(evaluate_rag.cleanup())
