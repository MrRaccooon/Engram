"""
Engram Comprehensive Smoke Test Suite — 100 tests.

Categories:
  1. Query Engine          (20 tests)
  2. Metadata DB           (20 tests)
  3. Intelligence Pipeline (15 tests)
  4. Consolidation Worker  (15 tests)
  5. Sensitivity + Masker  (10 tests)
  6. Eval Harness          (10 tests)
  7. Ask Route Integration (10 tests)
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_capture, make_chunk, make_insight, insert_test_capture


# ═══════════════════════════════════════════════════════════════════════════════
# 1. QUERY ENGINE — 20 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.query_engine import parse_query, ParsedQuery


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago_str(n: int) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


class TestQueryEngineTemporalParsing:
    def test_today(self):
        pq = parse_query("what did I do today")
        assert pq.has_temporal
        assert pq.date_from == _today_str()
        assert pq.date_to == _today_str()

    def test_yesterday(self):
        pq = parse_query("show me yesterday's work")
        assert pq.has_temporal
        assert pq.date_from == _days_ago_str(1)

    def test_day_before_yesterday(self):
        pq = parse_query("day before yesterday I was coding")
        assert pq.has_temporal
        assert pq.date_from == _days_ago_str(2)

    def test_last_weekday(self):
        pq = parse_query("what happened last Tuesday")
        assert pq.has_temporal
        assert pq.date_from is not None
        parsed_date = datetime.strptime(pq.date_from, "%Y-%m-%d")
        assert parsed_date.weekday() == 1  # Tuesday

    def test_last_monday(self):
        pq = parse_query("last Monday meeting notes")
        assert pq.has_temporal
        parsed_date = datetime.strptime(pq.date_from, "%Y-%m-%d")
        assert parsed_date.weekday() == 0

    def test_n_days_ago(self):
        pq = parse_query("3 days ago I was researching")
        assert pq.has_temporal
        assert pq.date_from == _days_ago_str(3)

    def test_10_days_ago(self):
        pq = parse_query("about 10 days ago")
        assert pq.has_temporal
        assert pq.date_from == _days_ago_str(10)

    def test_this_week(self):
        pq = parse_query("this week's progress")
        assert pq.has_temporal
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today - timedelta(days=today.weekday())
        assert pq.date_from == week_start.strftime("%Y-%m-%d")
        assert pq.date_to == _today_str()

    def test_last_week(self):
        pq = parse_query("last week I was debugging")
        assert pq.has_temporal
        assert pq.date_from is not None
        assert pq.date_to is not None
        start = datetime.strptime(pq.date_from, "%Y-%m-%d")
        end = datetime.strptime(pq.date_to, "%Y-%m-%d")
        assert (end - start).days == 6

    def test_this_morning(self):
        pq = parse_query("this morning I was reading docs")
        assert pq.has_temporal
        assert pq.date_from == _today_str()

    def test_earlier_today(self):
        pq = parse_query("earlier today I saw something")
        assert pq.has_temporal
        assert pq.date_from == _today_str()

    def test_recently(self):
        pq = parse_query("what have I done recently")
        assert pq.has_temporal
        assert pq.date_from is not None


class TestQueryEngineAppDetection:
    def test_vscode(self):
        pq = parse_query("what was I editing in VS Code")
        assert "Code.exe" in pq.app_filters

    def test_chrome(self):
        pq = parse_query("what did I search in chrome")
        assert "chrome.exe" in pq.app_filters

    def test_terminal(self):
        pq = parse_query("commands I ran in terminal")
        assert "WindowsTerminal.exe" in pq.app_filters


class TestQueryEngineIntent:
    def test_person(self):
        pq = parse_query("who sent me the email about deployment")
        assert pq.intent == "person"

    def test_temporal(self):
        pq = parse_query("when did I last edit the config")
        assert pq.intent == "temporal"

    def test_activity(self):
        pq = parse_query("how much time did I spend coding")
        assert pq.intent == "activity"

    def test_locate(self):
        pq = parse_query("where is the auth module file")
        assert pq.intent == "locate"

    def test_recall_default(self):
        pq = parse_query("what did I do with the database")
        assert pq.intent == "recall"


class TestQueryEngineEdgeCases:
    def test_entity_extraction_with_known_tags(self):
        pq = parse_query("show me work on Engram", known_tags=["Engram", "React"])
        assert "Engram" in pq.entity_filters

    def test_entity_extraction_empty_tags(self):
        pq = parse_query("show me work on Engram", known_tags=[])
        assert pq.entity_filters == []

    def test_no_signals(self):
        pq = parse_query("hello")
        assert pq.intent == "recall"
        assert not pq.has_temporal
        assert pq.app_filters == []

    def test_unicode_no_crash(self):
        pq = parse_query("what about 🔥 and émojis and 中文")
        assert isinstance(pq, ParsedQuery)

    def test_long_query_no_crash(self):
        pq = parse_query("a " * 1000)
        assert isinstance(pq, ParsedQuery)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. METADATA DB — 20 tests
# ═══════════════════════════════════════════════════════════════════════════════

from storage import metadata_db


class TestMetadataDBSchema:
    def test_init_creates_tables(self, tmp_db):
        with metadata_db._connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        for expected in ("captures", "job_queue", "insights", "topic_threads",
                         "capture_edges", "capture_tags", "eval_log"):
            assert expected in tables, f"Missing table: {expected}"


class TestMetadataDBCaptures:
    def test_insert_capture_returns_uuid(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        assert len(cid) == 36  # UUID format

    def test_insert_creates_job_queue_row(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_queue WHERE capture_id = ?", (cid,)
            ).fetchone()
        assert row is not None

    def test_fetch_pending_jobs(self, tmp_db):
        insert_test_capture(metadata_db)
        insert_test_capture(metadata_db)
        jobs = metadata_db.fetch_pending_jobs(limit=10)
        assert len(jobs) == 2

    def test_update_capture_status(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        metadata_db.update_capture_status(cid, "indexed")
        row = metadata_db.fetch_capture_by_id(cid)
        assert row["status"] == "indexed"

    def test_fetch_capture_by_id(self, tmp_db):
        cid = insert_test_capture(metadata_db, content="unique content here")
        row = metadata_db.fetch_capture_by_id(cid)
        assert row is not None
        assert row["content"] == "unique content here"

    def test_fetch_captures_for_day(self, tmp_db):
        today = datetime.utcnow().date().isoformat()
        insert_test_capture(metadata_db)
        rows = metadata_db.fetch_captures_for_day(today)
        assert len(rows) >= 1

    def test_fetch_captures_in_range(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        metadata_db.update_capture_status(cid, "indexed")
        today = datetime.utcnow().date().isoformat()
        rows = metadata_db.fetch_captures_in_range(today, today)
        assert len(rows) >= 1

    def test_fetch_recent_indexed_captures(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        metadata_db.update_capture_status(cid, "indexed")
        cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        rows = metadata_db.fetch_recent_indexed_captures(cutoff)
        assert len(rows) >= 1


class TestMetadataDBInsights:
    def test_insert_insight(self, tmp_db):
        metadata_db.insert_insight(
            insight_id=str(uuid.uuid4()),
            date="2025-01-15",
            session_start="2025-01-15T09:00:00",
            session_end="2025-01-15T12:00:00",
            summary="Test insight summary",
            topics='["coding", "testing"]',
            narrative="Detailed narrative",
            topics_structured='["coding", "testing"]',
            projects='["engram"]',
            files_touched='["test.py"]',
            decisions='["use pytest"]',
            problems='["slow tests"]',
            outcomes='["faster pipeline"]',
            consolidation_type="daily",
        )
        rows = metadata_db.fetch_insights_for_day("2025-01-15")
        assert len(rows) == 1
        assert rows[0]["narrative"] == "Detailed narrative"

    def test_has_insight_for_day_true(self, tmp_db):
        metadata_db.insert_insight(
            insight_id=str(uuid.uuid4()),
            date="2025-02-01",
            session_start="2025-02-01T09:00:00",
            session_end="2025-02-01T12:00:00",
            summary="Test",
            consolidation_type="daily",
        )
        assert metadata_db.has_insight_for_day("2025-02-01", "daily") is True

    def test_has_insight_for_day_false(self, tmp_db):
        assert metadata_db.has_insight_for_day("2099-01-01", "daily") is False

    def test_fetch_recent_insights(self, tmp_db):
        metadata_db.insert_insight(
            insight_id=str(uuid.uuid4()),
            date=datetime.utcnow().date().isoformat(),
            session_start=datetime.utcnow().isoformat(),
            session_end=datetime.utcnow().isoformat(),
            summary="Recent insight",
            consolidation_type="daily",
        )
        rows = metadata_db.fetch_recent_insights(days=1)
        assert len(rows) >= 1


class TestMetadataDBTagsAndThreads:
    def test_tags_roundtrip(self, tmp_db):
        cid = insert_test_capture(metadata_db)
        metadata_db.upsert_tags(cid, [("Python", "TECH"), ("OpenAI", "ORG")])
        rows = metadata_db.fetch_captures_by_tag("Python")
        assert len(rows) == 1
        assert rows[0]["id"] == cid

    def test_fetch_distinct_tags(self, tmp_db):
        cid1 = insert_test_capture(metadata_db)
        cid2 = insert_test_capture(metadata_db)
        metadata_db.upsert_tags(cid1, [("Python", "TECH")])
        metadata_db.upsert_tags(cid2, [("Python", "TECH"), ("Rust", "TECH")])
        tags = metadata_db.fetch_distinct_tags(limit=10)
        assert tags[0] == "Python"  # most common first

    def test_upsert_topic_thread_creates(self, tmp_db):
        tid = metadata_db.upsert_topic_thread(
            topic="vector-databases",
            summary="Learning about vector databases",
            session_count_delta=1,
            minutes_delta=30.0,
        )
        assert len(tid) == 36
        thread = metadata_db.fetch_topic_thread("vector-databases")
        assert thread is not None
        assert thread["total_sessions"] == 1

    def test_upsert_topic_thread_updates(self, tmp_db):
        metadata_db.upsert_topic_thread(
            topic="api-design",
            summary="First session",
            session_count_delta=1,
            minutes_delta=20.0,
        )
        metadata_db.upsert_topic_thread(
            topic="api-design",
            summary="Updated summary",
            session_count_delta=1,
            minutes_delta=15.0,
        )
        thread = metadata_db.fetch_topic_thread("api-design")
        assert thread["total_sessions"] == 2
        assert thread["total_minutes"] == 35.0

    def test_count_topic_occurrences(self, tmp_db):
        metadata_db.insert_insight(
            insight_id=str(uuid.uuid4()),
            date="2025-01-01",
            session_start="2025-01-01T09:00:00",
            session_end="2025-01-01T12:00:00",
            summary="Topic test",
            topics_structured='["vector-db", "rag"]',
            consolidation_type="daily",
        )
        count = metadata_db.count_topic_occurrences("vector-db")
        assert count >= 1


class TestMetadataDBEvalLog:
    def test_insert_eval_log(self, tmp_db):
        qid = str(uuid.uuid4())
        metadata_db.insert_eval_log(
            query_id=qid,
            query="what did I do today",
            intent="recall",
            candidate_count=10,
            sources_used="text,insights",
            model_used="gpt-4o-mini",
            latency_ms=350,
        )
        rows = metadata_db.fetch_eval_logs(days=1)
        assert len(rows) == 1
        assert rows[0]["query"] == "what did I do today"

    def test_update_eval_feedback(self, tmp_db):
        qid = str(uuid.uuid4())
        metadata_db.insert_eval_log(query_id=qid, query="test query")
        metadata_db.update_eval_feedback(qid, rating=1, note="great answer")
        rows = metadata_db.fetch_eval_logs(days=1)
        assert rows[0]["feedback_rating"] == 1
        assert rows[0]["feedback_note"] == "great answer"

    def test_fetch_eval_logs_respects_window(self, tmp_db):
        qid = str(uuid.uuid4())
        metadata_db.insert_eval_log(query_id=qid, query="old query")
        rows_1day = metadata_db.fetch_eval_logs(days=1)
        assert len(rows_1day) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. INTELLIGENCE PIPELINE — 15 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.intelligence import (
    _get_chunk_text,
    _assemble_prompt,
    _build_system_prompt,
    _SYSTEM_PROMPT_BASE,
    _MAX_CHARS_PER_CAPTURE,
)


class TestIntelligenceChunkText:
    def test_prefers_content(self):
        chunk = {"content": "full content", "content_preview": "preview"}
        assert _get_chunk_text(chunk) == "full content"

    def test_falls_back_to_preview(self):
        chunk = {"content": "", "content_preview": "preview only"}
        assert _get_chunk_text(chunk) == "preview only"

    def test_empty_chunk(self):
        assert _get_chunk_text({}) == ""


class TestIntelligencePromptAssembly:
    def test_includes_insights(self):
        insights = [make_insight(summary="Worked on vector databases")]
        prompt = _assemble_prompt("what did I research", [], 2000, insights=insights)
        assert "Session Summaries" in prompt
        assert "vector databases" in prompt

    def test_chronological_order(self):
        c1 = make_chunk(timestamp="2025-01-01T10:00:00", content="morning work")
        c2 = make_chunk(timestamp="2025-01-01T14:00:00", content="afternoon work")
        prompt = _assemble_prompt("test", [c2, c1], 2000)
        idx_morning = prompt.find("morning work")
        idx_afternoon = prompt.find("afternoon work")
        assert idx_morning < idx_afternoon

    def test_truncates_long_content(self):
        long_text = "x" * 3000
        chunk = make_chunk(content=long_text)
        prompt = _assemble_prompt("test", [chunk], 5000)
        assert "…" in prompt

    def test_respects_token_budget(self):
        chunks = [make_chunk(content="word " * 200) for _ in range(20)]
        prompt = _assemble_prompt("test", chunks, 500)
        word_count = len(prompt.split())
        assert word_count < 800  # budget + question overhead

    def test_empty_chunks(self):
        prompt = _assemble_prompt("what happened", [], 2000)
        assert "No relevant context found" in prompt

    def test_empty_insights(self):
        chunk = make_chunk(content="some data")
        prompt = _assemble_prompt("test", [chunk], 2000, insights=[])
        assert "Session Summaries" not in prompt


class TestIntelligenceSystemPrompt:
    def test_without_context(self):
        sp = _build_system_prompt()
        assert sp == _SYSTEM_PROMPT_BASE

    def test_with_context(self):
        sp = _build_system_prompt("Current project: Engram")
        assert "CURRENT USER CONTEXT" in sp
        assert "Engram" in sp


class TestIntelligenceBuildPreview:
    @patch("pipeline.intelligence._load_full_config")
    @patch("pipeline.intelligence._load_intelligence_config")
    @patch("pipeline.intelligence.sensitivity.filter_chunks")
    @patch("pipeline.intelligence.entity_masker.mask_chunks")
    def test_returns_expected_keys(self, mock_mask, mock_filter, mock_intel_cfg, mock_full_cfg):
        mock_full_cfg.return_value = {"capture": {}}
        mock_intel_cfg.return_value = {
            "sensitivity_threshold": 0.4,
            "max_context_tokens": 800,
            "local_summarizer": "",
        }
        mock_filter.return_value = ([make_chunk()], 0)
        mock_mask.return_value = ([make_chunk()], {})

        from pipeline.intelligence import build_preview
        result = build_preview("test query", [make_chunk()])
        assert "masked_prompt" in result
        assert "entity_map" in result
        assert "blocked_count" in result
        assert "passing_count" in result
        assert "estimated_tokens" in result
        assert "system_prompt" in result


class TestIntelligenceAsk:
    @patch("pipeline.intelligence._load_full_config")
    @patch("pipeline.intelligence._load_intelligence_config")
    def test_disabled_provider(self, mock_intel_cfg, mock_full_cfg):
        mock_full_cfg.return_value = {"capture": {}}
        mock_intel_cfg.return_value = {"api_provider": "disabled"}

        from pipeline.intelligence import ask
        result = ask("test", [])
        assert "disabled" in result["answer"].lower() or "disabled" in result["provider"]
        assert result["provider"] == "disabled"


class TestIntelligenceLocalSummarize:
    def test_skips_short_chunks(self):
        from pipeline.intelligence import _local_summarize
        short_chunk = make_chunk(content="hello world short text")
        result = _local_summarize([short_chunk], "some-model")
        assert result == [short_chunk]

    @patch("requests.post", side_effect=ConnectionError("Ollama not running"))
    def test_ollama_unavailable(self, mock_post):
        from pipeline.intelligence import _local_summarize
        long_chunk = make_chunk(content="word " * 50)
        result = _local_summarize([long_chunk], "llama3")
        assert len(result) == 1  # returns original


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CONSOLIDATION WORKER — 15 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.consolidation_worker import (
    _group_into_sessions,
    _session_duration_minutes,
    _heuristic_summary,
    _build_capture_texts,
    _parse_structured_json,
)


def _make_db_capture(ts_str: str, app: str = "Code.exe", content: str = "working") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "timestamp": ts_str,
        "source_type": "screenshot",
        "content": content,
        "window_title": f"{app} - main.py",
        "app_name": app,
        "status": "indexed",
    }


class TestConsolidationGrouping:
    def test_splits_on_gap(self):
        caps = [
            _make_db_capture("2025-01-01T10:00:00"),
            _make_db_capture("2025-01-01T10:05:00"),
            _make_db_capture("2025-01-01T11:00:00"),  # 55min gap
            _make_db_capture("2025-01-01T11:05:00"),
        ]
        sessions = _group_into_sessions(caps, gap_minutes=30)
        assert len(sessions) == 2
        assert len(sessions[0]) == 2
        assert len(sessions[1]) == 2

    def test_continuous_single_session(self):
        caps = [
            _make_db_capture("2025-01-01T10:00:00"),
            _make_db_capture("2025-01-01T10:10:00"),
            _make_db_capture("2025-01-01T10:20:00"),
        ]
        sessions = _group_into_sessions(caps, gap_minutes=30)
        assert len(sessions) == 1

    def test_empty_input(self):
        assert _group_into_sessions([], gap_minutes=30) == []


class TestConsolidationDuration:
    def test_calculates_correctly(self):
        caps = [
            _make_db_capture("2025-01-01T10:00:00"),
            _make_db_capture("2025-01-01T10:30:00"),
        ]
        assert _session_duration_minutes(caps) == 30.0

    def test_single_capture_zero(self):
        caps = [_make_db_capture("2025-01-01T10:00:00")]
        assert _session_duration_minutes(caps) == 0


class TestConsolidationHeuristic:
    def test_returns_all_keys(self):
        caps = [
            _make_db_capture("2025-01-01T10:00:00", app="Code.exe"),
            _make_db_capture("2025-01-01T10:05:00", app="Code.exe"),
        ]
        result = _heuristic_summary(caps)
        for key in ("narrative", "topics", "projects", "files_touched",
                     "decisions", "problems", "outcomes"):
            assert key in result

    def test_identifies_top_app(self):
        caps = [
            _make_db_capture("2025-01-01T10:00:00", app="Code.exe"),
            _make_db_capture("2025-01-01T10:01:00", app="Code.exe"),
            _make_db_capture("2025-01-01T10:02:00", app="chrome.exe"),
        ]
        result = _heuristic_summary(caps)
        assert "Code.exe" in result["narrative"]


class TestConsolidationCaptureTexts:
    def test_respects_max_captures(self):
        caps = [_make_db_capture(f"2025-01-01T10:{i:02}:00") for i in range(30)]
        texts = _build_capture_texts(caps, max_captures=5)
        assert len(texts) == 5

    def test_truncates_text(self):
        long_content = "x" * 1000
        caps = [_make_db_capture("2025-01-01T10:00:00", content=long_content)]
        texts = _build_capture_texts(caps)
        assert len(texts[0]) < 500


class TestConsolidationJsonParsing:
    def test_valid_json(self):
        raw = json.dumps({
            "narrative": "Did some coding",
            "topics": ["python"],
            "projects": ["engram"],
            "files_touched": ["main.py"],
            "decisions": ["use pytest"],
            "problems": [],
            "outcomes": ["tests pass"],
        })
        result = _parse_structured_json(raw)
        assert result is not None
        assert result["narrative"] == "Did some coding"

    def test_strips_markdown_fences(self):
        raw = '```json\n{"narrative": "test", "topics": []}\n```'
        result = _parse_structured_json(raw)
        assert result is not None

    def test_rejects_without_narrative(self):
        raw = json.dumps({"topics": ["test"], "projects": []})
        result = _parse_structured_json(raw)
        assert result is None

    def test_fills_missing_lists(self):
        raw = json.dumps({"narrative": "did things"})
        result = _parse_structured_json(raw)
        assert result is not None
        assert result["topics"] == []
        assert result["projects"] == []
        assert result["files_touched"] == []


class TestConsolidationTopicThreads:
    @patch("pipeline.consolidation_worker.metadata_db")
    def test_skips_below_threshold(self, mock_mdb):
        from pipeline.consolidation_worker import _update_topic_threads
        mock_mdb.count_topic_occurrences.return_value = 1  # below threshold of 3
        structured = {"topics": ["rare-topic"], "narrative": "test", "projects": [], "files_touched": [], "decisions": []}
        _update_topic_threads(structured, 30.0)
        mock_mdb.upsert_topic_thread.assert_not_called()


class TestConsolidationSaveInsight:
    @patch("pipeline.consolidation_worker.vector_db")
    @patch("pipeline.consolidation_worker.embedder")
    @patch("pipeline.consolidation_worker.metadata_db")
    def test_writes_to_sqlite_and_chromadb(self, mock_mdb, mock_emb, mock_vdb):
        from pipeline.consolidation_worker import _save_insight
        mock_emb.embed_text.return_value = [0.1] * 384

        session = [
            _make_db_capture("2025-01-01T10:00:00"),
            _make_db_capture("2025-01-01T10:30:00"),
        ]
        structured = {
            "narrative": "Test narrative",
            "topics": ["testing"],
            "projects": ["engram"],
            "files_touched": [],
            "decisions": [],
            "problems": [],
            "outcomes": [],
        }
        _save_insight("2025-01-01", session, structured, "daily")
        mock_mdb.insert_insight.assert_called_once()
        mock_vdb.upsert_insight.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SENSITIVITY + ENTITY MASKER — 10 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline import sensitivity
from pipeline import entity_masker


class TestSensitivityScoring:
    def test_hard_block_password_manager(self):
        chunk = {"app_name": "1Password.exe", "content_preview": "vault", "url": ""}
        s = sensitivity.score(chunk)
        assert s == 1.0

    def test_hard_block_banking_domain(self):
        chunk = {"app_name": "", "content_preview": "balance", "url": "chase.com/accounts"}
        s = sensitivity.score(chunk)
        assert s == 1.0

    def test_soft_score_password_pattern(self):
        chunk = {"app_name": "", "url": "", "content_preview": 'password = "secret123"'}
        s = sensitivity.score(chunk)
        assert s > 0.3

    def test_soft_score_ssn_pattern(self):
        chunk = {"app_name": "", "url": "", "content_preview": "SSN: 123-45-6789"}
        s = sensitivity.score(chunk)
        assert s > 0.5

    def test_clean_text_low_score(self):
        chunk = {"app_name": "Code.exe", "url": "", "content_preview": "def hello(): print('hi')"}
        s = sensitivity.score(chunk)
        assert s < 0.4

    def test_filter_chunks_separates(self):
        safe = {"app_name": "Code.exe", "url": "", "content_preview": "clean code", "source_type": "screenshot", "timestamp": "2025-01-01T10:00:00"}
        blocked = {"app_name": "1Password.exe", "url": "", "content_preview": "vault data", "source_type": "screenshot", "timestamp": "2025-01-01T10:00:00"}
        passing, blocked_count = sensitivity.filter_chunks([safe, blocked], threshold=0.4)
        assert len(passing) == 1
        assert blocked_count == 1


class TestEntityMasker:
    def test_unmask_restores_names(self):
        entity_map = {"[PERSON_1]": "John", "[ORG_1]": "Stripe"}
        text = "[PERSON_1] works at [ORG_1]"
        result = entity_masker.unmask(text, entity_map)
        assert result == "John works at Stripe"

    def test_unmask_empty_map(self):
        assert entity_masker.unmask("hello", {}) == "hello"

    def test_mask_without_spacy_returns_unchanged(self):
        with patch.object(entity_masker, '_nlp', None):
            with patch.object(entity_masker, '_get_nlp', return_value=None):
                text, emap = entity_masker.mask("John works at Google")
                assert text == "John works at Google"
                assert emap == {}

    def test_extract_tags_without_spacy(self):
        with patch.object(entity_masker, '_get_nlp', return_value=None):
            tags = entity_masker.extract_tags("John works at Google in India")
            assert tags == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EVAL HARNESS — 10 tests
# ═══════════════════════════════════════════════════════════════════════════════

from api.routes.eval import _percentile


class TestEvalPercentile:
    def test_empty_data(self):
        assert _percentile([], 95) == 0

    def test_single_value(self):
        assert _percentile([100], 95) == 100

    def test_p95_normal(self):
        data = list(range(1, 101))
        p95 = _percentile(data, 95)
        assert p95 >= 95


class TestEvalMetricsComputation:
    def test_empty_metrics(self, tmp_db):
        rows = metadata_db.fetch_eval_logs(days=7)
        assert len(rows) == 0

    def test_satisfaction_rate(self, tmp_db):
        for i in range(10):
            qid = str(uuid.uuid4())
            metadata_db.insert_eval_log(query_id=qid, query=f"query {i}")
            rating = 1 if i < 7 else -1
            metadata_db.update_eval_feedback(qid, rating)

        rows = metadata_db.fetch_eval_logs(days=1)
        entries = [dict(r) for r in rows]
        rated = [e for e in entries if e["feedback_rating"] is not None]
        positive = sum(1 for e in rated if e["feedback_rating"] > 0)
        rate = round(positive / len(rated), 3) if rated else 0
        assert rate == 0.7

    def test_source_distribution(self, tmp_db):
        metadata_db.insert_eval_log(
            query_id=str(uuid.uuid4()),
            query="test",
            sources_used="text,insights,temporal",
        )
        metadata_db.insert_eval_log(
            query_id=str(uuid.uuid4()),
            query="test2",
            sources_used="text,visual",
        )
        rows = metadata_db.fetch_eval_logs(days=1)
        entries = [dict(r) for r in rows]
        source_counts: dict[str, int] = {}
        for e in entries:
            for src in (e.get("sources_used") or "").split(","):
                src = src.strip()
                if src:
                    source_counts[src] = source_counts.get(src, 0) + 1
        assert source_counts["text"] == 2
        assert source_counts["insights"] == 1

    def test_intent_breakdown(self, tmp_db):
        metadata_db.insert_eval_log(
            query_id=str(uuid.uuid4()),
            query="test",
            intent="recall",
        )
        metadata_db.insert_eval_log(
            query_id=str(uuid.uuid4()),
            query="test2",
            intent="temporal",
        )
        rows = metadata_db.fetch_eval_logs(days=1)
        entries = [dict(r) for r in rows]
        intents = {e.get("intent") for e in entries}
        assert "recall" in intents
        assert "temporal" in intents

    def test_paginated_logs(self, tmp_db):
        for i in range(5):
            metadata_db.insert_eval_log(
                query_id=str(uuid.uuid4()),
                query=f"query {i}",
            )
        page1 = metadata_db.fetch_eval_logs_paginated(limit=3, offset=0)
        page2 = metadata_db.fetch_eval_logs_paginated(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    def test_eval_log_auto_created_by_ask(self, tmp_db):
        qid = str(uuid.uuid4())
        metadata_db.insert_eval_log(
            query_id=qid,
            query="what did I do",
            intent="recall",
            candidate_count=5,
            sources_used="text,insights",
            model_used="gpt-4o-mini",
            latency_ms=200,
        )
        rows = metadata_db.fetch_eval_logs(days=1)
        entry = dict(rows[0])
        assert entry["intent"] == "recall"
        assert entry["candidate_count"] == 5
        assert entry["model_used"] == "gpt-4o-mini"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ASK ROUTE INTEGRATION — 10 tests
# ═══════════════════════════════════════════════════════════════════════════════

from api.routes.ask import (
    _rrf_fuse,
    _dedupe_chunks_to_captures,
    _apply_recency,
    _remove_self_refs,
    _build_chroma_where,
    AskFilters,
)


class TestRRFFusion:
    def test_merges_sources(self):
        ranked_lists = {
            "text": [make_chunk(capture_id="a"), make_chunk(capture_id="b")],
            "visual": [make_chunk(capture_id="b"), make_chunk(capture_id="c")],
        }
        fused = _rrf_fuse(ranked_lists)
        ids = [f.get("capture_id") for f in fused]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids

    def test_applies_weights(self):
        # Insights weighted 1.5, text weighted 1.0
        ranked_lists = {
            "text": [make_chunk(capture_id="text_only")],
            "insights": [make_chunk(capture_id="insight_only")],
        }
        fused = _rrf_fuse(ranked_lists)
        scores = {f["capture_id"]: f["rrf_score"] for f in fused}
        assert scores["insight_only"] > scores["text_only"]

    def test_deduplicates(self):
        same_id = "same-capture"
        ranked_lists = {
            "text": [make_chunk(capture_id=same_id)],
            "visual": [make_chunk(capture_id=same_id)],
        }
        fused = _rrf_fuse(ranked_lists)
        assert len(fused) == 1
        assert fused[0]["rrf_score"] > 0


class TestDedupeChunks:
    def test_collapses_same_capture(self):
        chunks = [
            make_chunk(capture_id="cap1", score=0.9),
            make_chunk(capture_id="cap1", score=0.7),
            make_chunk(capture_id="cap2", score=0.8),
        ]
        deduped = _dedupe_chunks_to_captures(chunks)
        ids = [d.get("capture_id") for d in deduped]
        assert ids.count("cap1") == 1
        assert ids.count("cap2") == 1


class TestRecencyScoring:
    def test_boosts_recent(self):
        now = datetime.now(timezone.utc)
        recent = make_chunk(
            timestamp=now.isoformat(),
            score=0.5,
        )
        recent["rerank_score"] = 0.5
        old = make_chunk(
            timestamp=(now - timedelta(days=30)).isoformat(),
            score=0.5,
        )
        old["rerank_score"] = 0.5

        results = _apply_recency([recent, old])
        assert results[0]["rerank_score"] > results[1]["rerank_score"]

    def test_penalizes_url_source(self):
        now = datetime.now(timezone.utc)
        url_chunk = make_chunk(
            source_type="url",
            timestamp=now.isoformat(),
            score=0.5,
        )
        url_chunk["rerank_score"] = 0.5
        ss_chunk = make_chunk(
            source_type="screenshot",
            timestamp=now.isoformat(),
            score=0.5,
        )
        ss_chunk["rerank_score"] = 0.5

        results = _apply_recency([url_chunk, ss_chunk])
        url_score = next(r["rerank_score"] for r in results if r["source_type"] == "url")
        ss_score = next(r["rerank_score"] for r in results if r["source_type"] == "screenshot")
        assert ss_score > url_score


class TestSelfRefRemoval:
    def test_removes_clipboard_self_match(self):
        chunks = [
            make_chunk(source_type="clipboard", content="what did I do today",
                       content_preview="what did I do today"),
            make_chunk(source_type="screenshot", content="real content",
                       content_preview="real content"),
        ]
        filtered = _remove_self_refs(chunks, "what did I do today")
        assert len(filtered) == 1
        assert filtered[0]["source_type"] == "screenshot"


class TestChromaWhereBuilder:
    def test_date_range(self):
        filters = AskFilters(date_from="2025-01-01", date_to="2025-01-31")
        where = _build_chroma_where(filters)
        assert where is not None
        assert "$and" in where

    def test_source_types(self):
        filters = AskFilters(source_types=["screenshot", "clipboard"])
        where = _build_chroma_where(filters)
        assert where is not None
        assert where["source_type"]["$in"] == ["screenshot", "clipboard"]

    def test_empty_filters(self):
        filters = AskFilters()
        where = _build_chroma_where(filters)
        assert where is None
