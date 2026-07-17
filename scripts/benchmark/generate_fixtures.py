"""Generate deterministic test documents for capacity benchmarks.

Usage:
  python scripts/benchmark/generate_fixtures.py --all --output-dir fixtures_benchmark/
  python scripts/benchmark/generate_fixtures.py --scenario small_batch --output-dir fixtures_benchmark/
"""

import argparse
import csv
import hashlib
import io
import json
import random
import sys
from pathlib import Path

# Fixed seed per scenario for deterministic output
SCENARIO_SEEDS = {
    "small_batch": 1,
    "medium_batch": 2,
    "large_boundary": 3,
    "mixed_formats": 4,
    "partial_invalid": 5,
}

CHINESE_PARAGRAPHS = [
    "星河知识平台是一个面向企业级客户的知识管理与智能问答系统。"
    "它支持多种文档格式的导入、自动解析、向量化索引和基于大语言模型的精准问答。",
    "系统采用混合检索架构，结合语义向量检索和关键词 BM25 检索，"
    "通过倒数排名融合算法对两种检索结果进行加权排序，确保召回率和精确率的平衡。",
    "文档处理管线包括格式解析、文本切分、嵌入向量生成和索引写入四个阶段。"
    "每个阶段都有独立的错误处理和重试机制，确保单文件失败不影响批量处理的其他文件。",
    "平台提供管理控制台和 RESTful API 两种管理方式。"
    "管理员可以通过 Web 界面上传文档、查看处理状态、配置问答策略，"
    "也可以使用 API 集成到现有的企业工作流中。",
    "系统内置了基于引用验证的答案质量评估模块。"
    "每个生成的回答都会经过忠实度、引用精确率和引用完整率三个维度的自动评估，"
    "不满足质量门禁的回答会触发自动修复流程。",
    "数据安全方面，系统支持基于管理令牌的 API 鉴权，"
    "所有上传文档在传输和存储过程中均可配置加密保护。"
    "备份和恢复功能支持全量知识库的快照导出和跨环境迁移。",
]

MD_TEMPLATE = """# {title}

## 概述

{paragraphs}

## 详细说明

{details}

## 配置示例

```yaml
{config}
```

## 注意事项

- {note1}
- {note2}
- {note3}
"""


def _make_txt(target_bytes: int, seed: int) -> str:
    """Generate a TXT file of approximately target_bytes using Chinese text."""
    rng = random.Random(seed)
    parts = []
    total = 0
    while total < target_bytes:
        para = rng.choice(CHINESE_PARAGRAPHS)
        parts.append(para)
        total += len(para.encode("utf-8"))
    return "\n\n".join(parts)


def _make_md(target_bytes: int, seed: int) -> str:
    """Generate a Markdown file of approximately target_bytes."""
    rng = random.Random(seed)
    title = f"文档编号_{seed:04d}"
    paragraphs = "\n\n".join(rng.sample(CHINESE_PARAGRAPHS, min(3, len(CHINESE_PARAGRAPHS))))
    details = "\n\n".join(f"{i}. {rng.choice(CHINESE_PARAGRAPHS)}" for i in range(1, 5))
    config_lines = [f"  setting_{i}: {rng.randint(1, 1000)}" for i in range(5)]
    notes = rng.sample(CHINESE_PARAGRAPHS, 3)
    content = MD_TEMPLATE.format(
        title=title, paragraphs=paragraphs, details=details,
        config="\n".join(config_lines),
        note1=notes[0], note2=notes[1], note3=notes[2],
    )
    while len(content.encode("utf-8")) < target_bytes:
        content += f"\n\n{rng.choice(CHINESE_PARAGRAPHS)}"
    return content


def _make_csv(target_rows: int, seed: int) -> str:
    """Generate a CSV with header + target_rows data rows."""
    rng = random.Random(seed)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "category", "priority", "status", "created_at", "content"])
    for i in range(target_rows):
        writer.writerow([
            i + 1,
            f"事项_{rng.randint(1, 10000):05d}",
            rng.choice(["技术", "产品", "运营", "销售", "人事"]),
            rng.choice(["P0", "P1", "P2", "P3"]),
            rng.choice(["待处理", "进行中", "已完成", "已关闭"]),
            f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            rng.choice(CHINESE_PARAGRAPHS)[:80],
        ])
    return buf.getvalue()


def _compute_sha256(filepath: Path) -> str:
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def generate_scenario(scenario: str, output_dir: Path) -> dict:
    seed = SCENARIO_SEEDS[scenario]
    rng = random.Random(seed)
    out = output_dir / scenario
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"scenario": scenario, "documents": []}

    if scenario == "small_batch":
        for i in range(50):
            size = rng.randint(10_000, 100_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "medium_batch":
        for i in range(20):
            size = rng.randint(5_000_000, 20_000_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "large_boundary":
        for i in range(5):
            size = rng.randint(100_000_000, 200_000_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "mixed_formats":
        for i in range(30):
            fmt = rng.choice(["txt", "md", "csv"])
            size = rng.randint(5_000, 5_000_000)
            ext = {"txt": ".txt", "md": ".md", "csv": ".csv"}[fmt]
            fname = f"doc_{i:03d}{ext}"
            fpath = out / fname
            if fmt == "txt":
                fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            elif fmt == "md":
                fpath.write_text(_make_md(size, seed + i), encoding="utf-8")
            else:
                rows = max(10, size // 200)
                fpath.write_text(_make_csv(rows, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })

    elif scenario == "partial_invalid":
        for i in range(7):
            size = rng.randint(5_000, 50_000)
            fname = f"doc_{i:03d}.txt"
            fpath = out / fname
            fpath.write_text(_make_txt(size, seed + i), encoding="utf-8")
            manifest["documents"].append({
                "path": fpath.name, "size": fpath.stat().st_size,
                "sha256": _compute_sha256(fpath),
            })
        fake_exe = out / "readme.exe"
        fake_exe.write_text(_make_txt(10_000, seed + 7), encoding="utf-8")
        manifest["documents"].append({
            "path": "readme.exe", "size": fake_exe.stat().st_size,
            "sha256": _compute_sha256(fake_exe), "note": "wrong_extension",
        })
        empty = out / "empty.txt"
        empty.write_text("", encoding="utf-8")
        manifest["documents"].append({
            "path": "empty.txt", "size": 0,
            "sha256": _compute_sha256(empty), "note": "empty",
        })
        first = out / "doc_000.txt"
        dup = out / "duplicate.txt"
        dup.write_bytes(first.read_bytes())
        manifest["documents"].append({
            "path": "duplicate.txt", "size": dup.stat().st_size,
            "sha256": _compute_sha256(dup), "note": "duplicate_of_doc_000",
        })

    manifest_path = out.parent / f"manifest_{scenario}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    total_size = sum(doc["size"] for doc in manifest["documents"])
    print(f"[{scenario}] {len(manifest['documents'])} files, {total_size / 1024 / 1024:.1f} MB total")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark test documents")
    parser.add_argument("--scenario", help="Single scenario name")
    parser.add_argument("--all", action="store_true", help="Generate all scenarios")
    parser.add_argument("--output-dir", default="fixtures_benchmark", help="Output root directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = list(SCENARIO_SEEDS.keys()) if args.all else [args.scenario]
    for scenario in scenarios:
        if scenario not in SCENARIO_SEEDS:
            print(f"Unknown scenario: {scenario}", file=sys.stderr)
            sys.exit(1)
        generate_scenario(scenario, output_dir)

    if args.all:
        print(f"\nAll {len(scenarios)} scenarios generated in {output_dir.resolve()}")


if __name__ == "__main__":
    main()
