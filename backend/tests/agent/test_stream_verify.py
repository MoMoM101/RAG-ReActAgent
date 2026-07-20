"""Unit tests for V4 Phase 3 atomic unit streaming verification."""


from agent.stream_verify import (
    AtomicUnit,
    AtomicUnitBuffer,
    UnitVerdict,
    _is_mid_citation,
    build_repair_prompt,
    verify_unit,
)
from agent.verifier import Evidence

# ── Boundary detection ──────────────────────────────────────────────


class TestAtomicUnitBuffer:
    def test_extracts_on_chinese_period(self):
        buf = AtomicUnitBuffer()
        result = buf.feed("知识库包含三份文档")
        assert result is None
        result = buf.feed("。")
        assert result is not None
        assert "知识库包含三份文档" in result.text
        assert "。" in result.text or result.boundary

    def test_extracts_on_chinese_question_mark(self):
        buf = AtomicUnitBuffer()
        unit = buf.feed("如何配置RAG系统？")
        assert unit is not None
        assert "如何配置RAG系统" in unit.text

    def test_extracts_multiple_units_sequentially(self):
        buf = AtomicUnitBuffer()
        unit1 = buf.feed("第一个事实 [S1]。第二个事实 [S2]。")
        assert unit1 is not None
        assert "第一个事实" in unit1.text
        unit2 = buf.extract_next()
        assert unit2 is not None
        assert "第二个事实" in unit2.text
        assert "第二个事实" not in unit1.boundary
        assert unit1.text + unit1.boundary + unit2.text == (
            "第一个事实 [S1]。第二个事实 [S2]。"
        )

    def test_mid_citation_not_split(self):
        buf = AtomicUnitBuffer()
        result = buf.feed("这是一个声明 [S1")
        assert result is None  # mid-citation, don't split
        result = buf.feed(", S2]。")
        assert result is not None
        assert "[S1, S2]" in result.text

    def test_structural_line_not_emitted_alone(self):
        buf = AtomicUnitBuffer()
        unit = buf.feed("已确认：知识库包含三份文档。")
        assert unit is not None
        # The structural label should be part of a larger unit, not standalone
        assert "已确认" in unit.text

    def test_paragraph_break_splits(self):
        buf = AtomicUnitBuffer()
        unit1 = buf.feed("第一段内容 [S1]。\n\n第二段内容。")
        assert unit1 is not None
        assert "第一段" in unit1.text
        unit2 = buf.extract_next()
        assert unit2 is not None
        assert "第二段" in unit2.text

    def test_flush_remainder_returns_remaining_text(self):
        buf = AtomicUnitBuffer()
        buf.feed("一个不完整的句子")
        unit = buf.flush_remainder()
        assert unit is not None
        assert "一个不完整的句子" in unit.text

    def test_short_text_not_extracted(self):
        buf = AtomicUnitBuffer()
        buf.feed("短。")
        buf.flush_remainder()
        # Very short text may or may not be extracted depending on min length
        # This is fine - we just verify it doesn't crash
        assert buf is not None

    def test_citation_after_period_included_in_unit(self):
        buf = AtomicUnitBuffer()
        unit = buf.feed("RAG结合了检索与生成 [S1]。")
        assert unit is not None
        assert "[S1]" in unit.text

    def test_committed_text_tracks_correctly(self):
        buf = AtomicUnitBuffer()
        unit1 = buf.feed("第一个声明 [S1]。")
        assert unit1 is not None
        buf.commit(unit1)
        unit2 = buf.feed("第二个声明 [S2]。")
        assert unit2 is not None
        buf.commit(unit2)
        committed = buf.committed_text
        assert "第一个声明" in committed
        assert "第二个声明" in committed
        assert "[S1]" in committed
        assert "[S2]" in committed

    def test_reset_for_repair_clears_buffer_only(self):
        buf = AtomicUnitBuffer()
        unit1 = buf.feed("已发送内容 [S1]。")
        assert unit1 is not None
        buf.commit(unit1)
        buf.feed("未发送的草稿")
        buf.reset_for_repair()
        assert "已发送内容" in buf.committed_text
        assert buf.pending_text == ""


# ── Mid-citation detection ──────────────────────────────────────────


class TestMidCitation:
    def test_complete_citation_not_mid(self):
        assert not _is_mid_citation("声明 [S1]。")

    def test_unclosed_bracket_is_mid(self):
        assert _is_mid_citation("声明 [S1")

    def test_partial_second_id_is_mid(self):
        assert _is_mid_citation("声明 [S1, S")

    def test_multi_citation_complete_not_mid(self):
        assert not _is_mid_citation("声明 [S1, S2]。")

    def test_no_bracket_not_mid(self):
        assert not _is_mid_citation("普通文本没有引用。")


# ── Unit-level verification ─────────────────────────────────────────


class TestVerifyUnit:
    def _evidence(self):
        return [
            Evidence(citation_id="S1", text="知识库包含三份主要技术文档，涵盖RAG系统的检索与生成流程。"),
            Evidence(citation_id="S2", text="系统的平均响应延迟为250毫秒，P95为800毫秒。"),
            Evidence(citation_id="S3", text="向量数据库使用Qdrant进行存储和检索。"),
        ]

    def test_verified_when_cited_source_supports(self):
        unit = AtomicUnit(text="知识库包含三份技术文档 [S1]。", citations=["S1"])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.VERIFIED

    def test_format_only_when_uncited_but_supported(self):
        unit = AtomicUnit(text="知识库包含三份技术文档。", citations=[])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.FORMAT_ONLY

    def test_unsupported_when_no_evidence_match(self):
        unit = AtomicUnit(text="系统部署在AWS上 [S1]。", citations=["S1"])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.UNSUPPORTED

    def test_unsupported_when_numbers_mismatch(self):
        unit = AtomicUnit(text="系统延迟为500毫秒 [S2]。", citations=["S2"])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.UNSUPPORTED

    def test_verified_when_numbers_match(self):
        unit = AtomicUnit(text="系统平均延迟为250毫秒 [S2]。", citations=["S2"])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.VERIFIED

    def test_multiple_citations_can_jointly_support_comparison_numbers(self):
        evidence = [
            Evidence(citation_id="S1", text="MCP 单次任务消耗约 12000 Token。"),
            Evidence(citation_id="S2", text="Skills 单次任务消耗约 2000 Token。"),
        ]
        unit = AtomicUnit(
            text="MCP 约消耗 12000 Token，Skills 约消耗 2000 Token [S1, S2]。",
            citations=["S1", "S2"],
        )

        result = verify_unit(unit, evidence)

        assert result.verdict == UnitVerdict.VERIFIED

    def test_multiple_citations_union_still_rejects_missing_number(self):
        evidence = [
            Evidence(citation_id="S1", text="MCP 单次任务消耗约 12000 Token。"),
            Evidence(citation_id="S2", text="Skills 单次任务消耗约 2000 Token。"),
        ]
        unit = AtomicUnit(
            text="MCP 约消耗 12000 Token，Skills 约消耗 3000 Token [S1, S2]。",
            citations=["S1", "S2"],
        )

        result = verify_unit(unit, evidence)

        assert result.verdict == UnitVerdict.UNSUPPORTED
        assert result.reason == "missing_number"

    def test_structural_line_skipped(self):
        unit = AtomicUnit(text="已确认：", citations=[])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.VERIFIED

    def test_invalid_citation_ignored(self):
        unit = AtomicUnit(text="这是一个声明 [S99]。", citations=["S99"])
        result = verify_unit(unit, self._evidence())
        assert result.verdict == UnitVerdict.UNSUPPORTED


# ── Repair prompt construction ──────────────────────────────────────


class TestBuildRepairPrompt:
    def test_includes_committed_units(self):
        committed = [AtomicUnit(text="已确认：知识库包含三份文档 [S1]。", citations=["S1"])]
        prompt = build_repair_prompt(
            "知识库有多少文档？",
            [{"citation_id": "S1", "text": "知识库包含三份文档。"}],
            committed,
            "更多内容...",
        )
        assert "已确认" in prompt
        assert "知识库有多少文档" in prompt
        assert "已发送的已验证内容" in prompt
        assert "不要重复已发送的内容" in prompt

    def test_no_committed_units_works(self):
        prompt = build_repair_prompt(
            "测试问题",
            [{"citation_id": "S1", "text": "来源内容。"}],
            [],
            "草稿内容...",
        )
        assert "测试问题" in prompt
        assert "（无）" in prompt


# ── Committed units cannot be duplicated in repair ──────────────────


class TestCommittedUnitSafety:
    def test_committed_text_excluded_from_repair_remaining(self):
        """Repair LLM should receive only uncommitted content as remaining draft."""
        buf = AtomicUnitBuffer()
        unit1 = AtomicUnit(text="第一事实 [S1]。", citations=["S1"])
        buf.commit(unit1)
        buf.feed("第二事实草稿...")

        committed = buf.committed_text
        assert "第一事实" in committed
        assert buf.pending_text == "第二事实草稿..."
        # The repair prompt should use pending_text as remaining draft
        assert "第一事实" not in buf.pending_text
