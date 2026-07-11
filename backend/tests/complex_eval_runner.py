"""Complex-domain eval: 9 overlapping documents, 30 disambiguation queries.

3 domains x 3 docs each — documents share terminology but differ in specifics.
Tests whether keyword can disambiguate where semantic would confuse similar concepts.
"""
import asyncio
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from evaluate_rag import QueryCase, TestDoc, save_results

from config import settings

DATA_DIR = Path(__file__).resolve().parent / "eval_data"

# 9 overlapping documents across 3 domains
DOC_FILES = [
    # Domain 0: Payment APIs (3 docs sharing "refund/order/webhook" concepts)
    "api_paygate.txt",    # doc 0
    "api_stripepay.txt",  # doc 1
    "api_fastpay.txt",    # doc 2
    # Domain 1: Humidity sensors (3 docs sharing specs/ranges/interfaces)
    "sensor_sht4x.txt",   # doc 3
    "sensor_hdc3x.txt",   # doc 4
    "sensor_bme280.txt",  # doc 5
    # Domain 2: Data compliance (3 docs sharing "consent/deletion/breach")
    "compliance_gdpr.txt",    # doc 6
    "compliance_pipl.txt",    # doc 7
    "compliance_pci_dss.txt", # doc 8
]


def load_docs():
    docs = []
    for fname in DOC_FILES:
        content = (DATA_DIR / fname).read_text(encoding="utf-8")
        docs.append(TestDoc(filename=fname, content=content))
    return docs


# 30 queries designed for disambiguation
# Key: within each domain, same concept appears in multiple docs
# but with different specifics (error codes, accuracy, thresholds)
QUERY_CASES = [
    # ===== DISAMBIGUATION: API error codes (keyword critical) =====
    QueryCase(query="ERR_40005", relevant_chunk_indices=[1], doc_index=0,
              expected_keywords=["ERR_40005", "status not PAID"]),
    QueryCase(query="REF_001", relevant_chunk_indices=[1], doc_index=1,
              expected_keywords=["REF_001", "payment not found"]),
    QueryCase(query="Txn_1003", relevant_chunk_indices=[0], doc_index=2,
              expected_keywords=["Txn_1003", "amount", "2M CNY"]),
    # Similar error codes across APIs — need to find the RIGHT API doc
    QueryCase(query="refund error code for merchant balance insufficient",
              relevant_chunk_indices=[0], doc_index=0,
              expected_keywords=["ERR_40201", "balance low"],
              cross_doc_targets={0: [0], 1: [1]}),
    QueryCase(query="refund error code when balance insufficient",
              relevant_chunk_indices=[1], doc_index=1,
              expected_keywords=["REF_004", "balance insufficient"],
              cross_doc_targets={1: [1]}),
    QueryCase(query="refund error credit insufficient",
              relevant_chunk_indices=[1], doc_index=2,
              expected_keywords=["Ref_2004", "credit insufficient"]),

    # ===== DISAMBIGUATION: Sensor specs (overlapping but different) =====
    QueryCase(query="humidity sensor +/-1.0% RH accuracy or better",
              relevant_chunk_indices=[2], doc_index=3,
              expected_keywords=["SHT45", "+/-1.0%", "RH"],
              cross_doc_targets={3: [2], 4: [1, 2]}),
    QueryCase(query="HDC3022 accuracy specifications",
              relevant_chunk_indices=[2], doc_index=4,
              expected_keywords=["HDC3022", "+/-0.8%", "RH"]),
    QueryCase(query="BME280 pressure sensor accuracy",
              relevant_chunk_indices=[0], doc_index=5,
              expected_keywords=["BME280", "+/-1.0 hPa", "pressure"]),
    QueryCase(query="sensor with PTFE membrane for automotive",
              relevant_chunk_indices=[2], doc_index=3,
              expected_keywords=["SHT45", "PTFE", "AEC-Q100"]),
    QueryCase(query="sensor with integrated heater for condensation",
              relevant_chunk_indices=[0], doc_index=4,
              expected_keywords=["HDC3020", "heater", "condensation"]),
    QueryCase(query="which sensor has integrated IIR filter",
              relevant_chunk_indices=[0], doc_index=5,
              expected_keywords=["BME280", "IIR filter"]),
    QueryCase(query="I2C address 0x76 sensor",
              relevant_chunk_indices=[0], doc_index=5,
              expected_keywords=["BME280", "0x76", "I2C"]),
    QueryCase(query="NIST traceable humidity sensor",
              relevant_chunk_indices=[1], doc_index=4,
              expected_keywords=["HDC3021", "NIST"]),

    # ===== DISAMBIGUATION: Compliance clauses (similar concepts, different details) =====
    QueryCase(query="72 hours data breach notification requirement",
              relevant_chunk_indices=[2], doc_index=6,
              expected_keywords=["Art.33", "72 hours", "supervisory authority"],
              cross_doc_targets={6: [2]}),
    QueryCase(query="immediate breach notification to affected individuals",
              relevant_chunk_indices=[2], doc_index=7,
              expected_keywords=["Art.57", "immediately", "notify"]),
    QueryCase(query="PAN masking first 6 last 4 digits",
              relevant_chunk_indices=[0], doc_index=8,
              expected_keywords=["PCI DSS", "PAN", "first 6", "last 4", "3.3"]),
    QueryCase(query="deletion right when consent withdrawn",
              relevant_chunk_indices=[0], doc_index=6,
              expected_keywords=["Art.17", "erasure", "GDPR"],
              cross_doc_targets={6: [0, 1], 7: [1]}),
    QueryCase(query="deletion right when purpose achieved retention expired",
              relevant_chunk_indices=[1], doc_index=7,
              expected_keywords=["Art.47", "deletion", "PIPL"]),
    QueryCase(query="right to data portability in machine readable format",
              relevant_chunk_indices=[1], doc_index=6,
              expected_keywords=["Art.20", "portability", "machine-readable"]),
    QueryCase(query="50 million CNY fine for violation",
              relevant_chunk_indices=[2], doc_index=7,
              expected_keywords=["50M", "CNY", "5%", "PIPL"]),
    QueryCase(query="20 million EUR fine 4% of global turnover",
              relevant_chunk_indices=[2], doc_index=6,
              expected_keywords=["20M", "EUR", "4%", "GDPR"]),

    # ===== NATURAL LANGUAGE: semantic should dominate =====
    QueryCase(query="what authentication methods do payment APIs support",
              relevant_chunk_indices=[0], doc_index=0,
              expected_keywords=["HMAC-SHA256", "RSA-SHA256", "API_KEY"],
              cross_doc_targets={0: [0], 1: [0], 2: [0]}),
    QueryCase(query="what is the maximum payment amount allowed per transaction",
              relevant_chunk_indices=[0], doc_index=0,
              expected_keywords=["1,000,000 CNY", "500K CNY", "2M CNY"],
              cross_doc_targets={0: [0], 1: [0], 2: [0]}),
    QueryCase(query="how to handle a refund when payment is not yet confirmed",
              relevant_chunk_indices=[1], doc_index=0,
              expected_keywords=["ERR_40005", "REF_003", "Ref_2002"],
              cross_doc_targets={0: [1], 1: [1], 2: [1]}),
    QueryCase(query="which sensors can operate at -40C to +125C temperature range",
              relevant_chunk_indices=[0], doc_index=3,
              expected_keywords=["SHT4x", "-40C", "125C"],
              cross_doc_targets={3: [0, 1, 2], 4: [0, 1, 2]}),

    # ===== SHORT KEYWORD: ambiguous terms across domains =====
    QueryCase(query="refund", relevant_chunk_indices=[0], doc_index=0,
              cross_doc_targets={0: [1], 1: [1], 2: [1]}),
    QueryCase(query="encryption", relevant_chunk_indices=[0], doc_index=8,
              cross_doc_targets={8: [0, 1], 6: [0], 7: [0]}),
    QueryCase(query="accuracy", relevant_chunk_indices=[0], doc_index=3,
              cross_doc_targets={3: [0, 1, 2], 4: [0, 1, 2], 5: [0, 1]}),
]


if __name__ == "__main__":
    # Use best sweep config
    settings.rrf_k = 30
    settings.rerank_top_n = 24
    settings.chunk_quality_filter_enabled = True
    settings.dedup_enabled = True
    # Enable weighted RRF (semantic=2.0, keyword=1.0) — this is default

    import evaluate_rag
    evaluate_rag.TEST_DOCS = load_docs()
    evaluate_rag.QUERY_CASES = QUERY_CASES

    try:
        results = asyncio.run(evaluate_rag.run_evaluation())
        save_results(results, "evaluation_results_complex_v2.json")
    finally:
        asyncio.run(evaluate_rag.cleanup())
