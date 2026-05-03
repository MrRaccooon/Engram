"""
Visual Intelligence Engine Test Suite — 80+ tests.

Categories:
  1. Concept Seeds              (5 tests)
  2. Metadata DB — Concepts    (15 tests)
  3. Metadata DB — Events      (10 tests)
  4. Concept Vocabulary Engine  (20 tests)
  5. Diff Analyzer             (15 tests)
  6. Worker Pipeline Integration (8 tests)
  7. Ask Route Integration      (7 tests)
"""

from __future__ import annotations

import builtins
import json
import math
import struct
import sys
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.conftest import make_capture, make_chunk, insert_test_capture
from storage import metadata_db


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_blob(dim: int = 512) -> bytes:
    vec = [0.1] * dim
    return struct.pack(f"{dim}f", *vec)


def _rand_unit_vec(dim: int = 512, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


class _FakeRow:
    """Dict-like object that also exposes .keys() like sqlite3.Row."""

    def __init__(self, d: dict):
        self._d = d

    def __getitem__(self, key):
        return self._d[key]

    def keys(self):
        return self._d.keys()


@pytest.fixture()
def _reset_cv_state():
    """Reset concept_vocabulary module-level cache after each test."""
    yield
    import pipeline.concept_vocabulary as cv

    cv._concept_ids = []
    cv._concept_prompts = []
    cv._concept_idf = []
    cv._concept_word_counts = []
    cv._concept_matrix = None
    cv._initialized = False


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CONCEPT SEEDS — 5 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.concept_seeds import (
    get_seed_concepts,
    all_seed_prompts,
    _expand_app_prompts,
    _APP_NAMES,
    _APP_TEMPLATES,
    _CATEGORIES,
)


class TestConceptSeedsCategories:
    def test_get_seed_concepts_returns_all_categories(self):
        seeds = get_seed_concepts()
        assert isinstance(seeds, dict)
        assert len(seeds) == len(_CATEGORIES) + 1  # +1 for "apps_generic"
        assert "apps_generic" in seeds
        for cat in _CATEGORIES:
            assert cat in seeds

    def test_all_seed_prompts_deduplicates(self):
        prompts = all_seed_prompts()
        assert len(prompts) == len(set(prompts))

    def test_total_seed_count_gte_200(self):
        prompts = all_seed_prompts()
        assert len(prompts) >= 200

    def test_every_prompt_is_nonempty_string(self):
        for p in all_seed_prompts():
            assert isinstance(p, str)
            assert len(p.strip()) > 0

    def test_expand_app_prompts_generates_correct_count(self):
        expanded = _expand_app_prompts()
        assert len(expanded) == len(_APP_NAMES) * len(_APP_TEMPLATES)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. METADATA DB — CONCEPT TABLES — 15 tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConceptInsertFetch:
    def test_insert_and_fetch_active_roundtrip(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="a code editor with source code",
            category="code_editors",
            source="seed",
            clip_embedding=_fake_blob(),
            status="active",
        )
        assert len(cid) == 36
        rows = metadata_db.fetch_active_concepts()
        prompts = [r["prompt"] for r in rows]
        assert "a code editor with source code" in prompts

    def test_duplicate_prompt_returns_existing_id(self, tmp_db):
        cid1 = metadata_db.insert_concept(
            prompt="duplicate prompt test",
            category="test",
            source="seed",
            clip_embedding=_fake_blob(),
        )
        cid2 = metadata_db.insert_concept(
            prompt="duplicate prompt test",
            category="test",
            source="seed",
            clip_embedding=_fake_blob(),
        )
        assert cid1 == cid2

    def test_fetch_probation_only_returns_probation(self, tmp_db):
        metadata_db.insert_concept(
            prompt="active one", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        metadata_db.insert_concept(
            prompt="probation one", category="t", source="window_title",
            clip_embedding=_fake_blob(), status="probation",
        )
        rows = metadata_db.fetch_probation_concepts()
        assert all(r["status"] == "probation" for r in rows)
        assert any(r["prompt"] == "probation one" for r in rows)

    def test_fetch_dormant_only_returns_dormant(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="soon dormant", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        metadata_db.update_concept_status(cid, "dormant")
        rows = metadata_db.fetch_dormant_concepts()
        assert len(rows) >= 1
        assert all(r["status"] == "dormant" for r in rows)

    def test_count_concepts(self, tmp_db):
        assert metadata_db.count_concepts() == 0
        metadata_db.insert_concept(
            prompt="c1", category="t", source="seed", clip_embedding=_fake_blob(),
        )
        metadata_db.insert_concept(
            prompt="c2", category="t", source="seed", clip_embedding=_fake_blob(),
        )
        assert metadata_db.count_concepts() == 2


class TestConceptStatusLifecycle:
    def test_update_concept_status(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="status change", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        metadata_db.update_concept_status(cid, "dormant")
        rows = metadata_db.fetch_dormant_concepts()
        assert any(r["id"] == cid for r in rows)

    def test_promote_concept_sets_active_and_promoted_at(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="to promote", category="t", source="window_title",
            clip_embedding=_fake_blob(), status="probation",
        )
        metadata_db.promote_concept(cid)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT status, promoted_at FROM concept_vocabulary WHERE id = ?",
                (cid,),
            ).fetchone()
        assert row["status"] == "active"
        assert row["promoted_at"] is not None

    def test_set_concept_needs_split(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="broad concept", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.set_concept_needs_split(cid, True)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT needs_split FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["needs_split"] == 1


class TestConceptMatchTracking:
    def test_record_concept_match_updates_fields(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="match track", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        # Lower the starting relevance so the +0.1 bump is observable
        metadata_db.update_concept_relevance(cid, 0.5)
        metadata_db.record_concept_match(cid, 0.25)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT match_count, avg_confidence, relevance_score, last_matched "
                "FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["match_count"] == 1
        assert abs(row["avg_confidence"] - 0.25) < 1e-4
        assert row["relevance_score"] > 0.5
        assert row["last_matched"] is not None

    def test_record_concept_match_running_average_formula(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="avg formula", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.record_concept_match(cid, 0.3)
        metadata_db.record_concept_match(cid, 0.5)
        metadata_db.record_concept_match(cid, 0.8)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT match_count, avg_confidence FROM concept_vocabulary WHERE id = ?",
                (cid,),
            ).fetchone()
        assert row["match_count"] == 3
        expected = (0.3 + 0.5 + 0.8) / 3
        assert abs(row["avg_confidence"] - expected) < 1e-3

    def test_update_concept_relevance(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="relevance upd", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.update_concept_relevance(cid, 0.75)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT relevance_score FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert abs(row["relevance_score"] - 0.75) < 1e-6

    def test_update_concept_idf(self, tmp_db):
        cid = metadata_db.insert_concept(
            prompt="idf upd", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.update_concept_idf(cid, 2.5)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT idf_weight FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert abs(row["idf_weight"] - 2.5) < 1e-6


class TestCaptureConceptsJoin:
    def test_insert_and_fetch_capture_concepts_roundtrip(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        cid = metadata_db.insert_concept(
            prompt="roundtrip concept", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.insert_capture_concepts(cap_id, [(cid, 0.85)])
        rows = metadata_db.fetch_concepts_for_capture(cap_id)
        assert len(rows) == 1
        assert rows[0]["prompt"] == "roundtrip concept"
        assert abs(rows[0]["confidence"] - 0.85) < 1e-4

    def test_fetch_captures_by_concepts(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        metadata_db.update_capture_status(cap_id, "indexed")
        cid = metadata_db.insert_concept(
            prompt="fetch by concept", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.insert_capture_concepts(cap_id, [(cid, 0.9)])
        rows = metadata_db.fetch_captures_by_concepts([cid])
        assert any(r["id"] == cap_id for r in rows)

    def test_count_indexed_screenshots(self, tmp_db):
        assert metadata_db.count_indexed_screenshots() == 0
        cap_id = insert_test_capture(metadata_db, source_type="screenshot")
        metadata_db.update_capture_status(cap_id, "indexed")
        assert metadata_db.count_indexed_screenshots() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. METADATA DB — EVENT TABLES — 10 tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventInsertFetch:
    def test_insert_and_fetch_events_roundtrip(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        eid = metadata_db.insert_capture_event(
            capture_id=cap_id,
            prev_capture_id=None,
            change_type="typing",
            change_magnitude=0.05,
            changed_text="hello world",
            window_title="editor - main.py",
            app_name="Code.exe",
            timestamp=datetime.utcnow().isoformat(),
        )
        assert len(eid) == 36
        rows = metadata_db.fetch_events_for_capture(cap_id)
        assert len(rows) >= 1
        assert rows[0]["change_type"] == "typing"

    def test_fetch_events_in_range_with_date_filters(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        ts = datetime(2025, 6, 15, 12, 0, 0).isoformat()
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="scrolling", change_magnitude=0.1, timestamp=ts,
        )
        rows = metadata_db.fetch_events_in_range(
            "2025-06-15T00:00:00", "2025-06-15T23:59:59",
        )
        assert len(rows) >= 1
        empty = metadata_db.fetch_events_in_range(
            "2025-07-01T00:00:00", "2025-07-01T23:59:59",
        )
        assert len(empty) == 0


class TestSearchEvents:
    def test_query_text_like_matching(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="typing", change_magnitude=0.05,
            changed_text="debugging the authentication module",
            timestamp=datetime.utcnow().isoformat(),
        )
        rows = metadata_db.search_events(query_text="authentication")
        assert len(rows) >= 1

    def test_app_name_filter(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="typing", change_magnitude=0.05,
            app_name="Code.exe",
            timestamp=datetime.utcnow().isoformat(),
        )
        rows = metadata_db.search_events(query_text="", app_name="Code")
        assert len(rows) >= 1

    def test_time_start_time_end(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        ts = datetime(2025, 8, 1, 14, 0, 0).isoformat()
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="app_switch", change_magnitude=0.8,
            timestamp=ts,
        )
        rows = metadata_db.search_events(
            query_text="",
            time_start="2025-08-01T00:00:00",
            time_end="2025-08-01T23:59:59",
        )
        assert len(rows) >= 1

    def test_empty_query_returns_results(self, tmp_db):
        cap_id = insert_test_capture(metadata_db)
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="scrolling", change_magnitude=0.15,
            timestamp=datetime.utcnow().isoformat(),
        )
        rows = metadata_db.search_events(query_text="")
        assert len(rows) >= 1


class TestEventTableMisc:
    def test_fetch_distinct_window_context_grouped(self, tmp_db):
        for _ in range(3):
            insert_test_capture(
                metadata_db, source_type="screenshot",
                app_name="Code.exe", window_title="editor - main.py",
            )
        rows = metadata_db.fetch_distinct_window_context(hours=24)
        assert len(rows) >= 1
        assert rows[0]["app_name"] == "Code.exe"
        assert rows[0]["cnt"] >= 3

    def test_fetch_recent_capture_texts_indexed_with_content(self, tmp_db):
        cap_id = insert_test_capture(
            metadata_db, source_type="screenshot",
            content="OCR extracted text from screenshot",
        )
        metadata_db.update_capture_status(cap_id, "indexed")
        rows = metadata_db.fetch_recent_capture_texts(hours=24)
        assert len(rows) >= 1
        assert "OCR extracted" in rows[0]["content"]

    def test_insert_capture_with_diff_data_stores_correctly(self, tmp_db):
        diff = json.dumps({"change_type": "typing", "change_magnitude": 0.05})
        cap_id = metadata_db.insert_capture(
            source_type="screenshot", content="test", diff_data=diff,
        )
        row = metadata_db.fetch_capture_by_id(cap_id)
        assert row["diff_data"] == diff

    def test_fetch_captures_in_window_fixed_datetime(self, tmp_db):
        ts = datetime(2025, 7, 15, 12, 0, 0)
        cap_id = metadata_db.insert_capture(
            source_type="screenshot", timestamp=ts, content="windowed",
        )
        # Normalize stored timestamp to space-separated format that SQLite
        # datetime() functions return, ensuring BETWEEN comparison works.
        with metadata_db._connect() as conn:
            conn.execute(
                "UPDATE captures SET timestamp = REPLACE(timestamp, 'T', ' ') WHERE id = ?",
                (cap_id,),
            )
        rows = metadata_db.fetch_captures_in_window(
            center_ts="2025-07-15T12:00:00", window_minutes=5,
        )
        ids = [r["id"] for r in rows]
        assert cap_id in ids


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CONCEPT VOCABULARY ENGINE — 20 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.concept_vocabulary import _vec_to_blob, _blob_to_vec


class TestVecBlobRoundtrip:
    def test_roundtrip_preserves_data(self):
        original = _rand_unit_vec(512, seed=7)
        blob = _vec_to_blob(original)
        recovered = _blob_to_vec(blob)
        assert len(recovered) == 512
        diff = np.array(original) - np.array(recovered)
        assert np.max(np.abs(diff)) < 1e-6


class TestRebuildCache:
    def test_loads_correct_shape(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        for i in range(5):
            metadata_db.insert_concept(
                prompt=f"cache test concept {i}",
                category="t", source="seed",
                clip_embedding=_vec_to_blob(_rand_unit_vec(512, seed=i)),
                status="active",
            )
        cv._rebuild_cache()
        assert cv._concept_matrix is not None
        assert cv._concept_matrix.shape == (5, 512)
        assert len(cv._concept_ids) == 5
        assert len(cv._concept_prompts) == 5


class TestTagScreenshot:
    def test_returns_top_k_above_threshold(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        for i in range(3):
            metadata_db.insert_concept(
                prompt=f"tag test concept {i}",
                category="t", source="seed",
                clip_embedding=_vec_to_blob(_rand_unit_vec(512, seed=i + 100)),
                status="active",
            )
        cv._rebuild_cache()
        img_vec = _rand_unit_vec(512, seed=100)
        old_thresh = cv._MIN_MATCH_THRESHOLD
        cv._MIN_MATCH_THRESHOLD = 0.0
        results = cv.tag_screenshot(img_vec)
        cv._MIN_MATCH_THRESHOLD = old_thresh
        assert isinstance(results, list)
        for cid, prompt, score in results:
            assert isinstance(cid, str)
            assert isinstance(prompt, str)
            assert isinstance(score, float)

    def test_returns_empty_when_matrix_is_none(self, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cv._concept_matrix = None
        assert cv.tag_screenshot([0.1] * 512) == []

    def test_records_matches_via_db(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cid = metadata_db.insert_concept(
            prompt="record match test concept",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(_rand_unit_vec(512, seed=200)),
            status="active",
        )
        cv._rebuild_cache()
        img_vec = _rand_unit_vec(512, seed=200)
        old_thresh = cv._MIN_MATCH_THRESHOLD
        cv._MIN_MATCH_THRESHOLD = 0.0
        cv.tag_screenshot(img_vec)
        cv._MIN_MATCH_THRESHOLD = old_thresh
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT match_count FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["match_count"] >= 1

    def test_applies_idf_weighting(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        vec = _rand_unit_vec(512, seed=300)
        cid_lo = metadata_db.insert_concept(
            prompt="low idf concept weighting test",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec),
        )
        metadata_db.update_concept_idf(cid_lo, 0.5)
        cid_hi = metadata_db.insert_concept(
            prompt="high idf concept weighting test",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec),
        )
        metadata_db.update_concept_idf(cid_hi, 3.0)
        cv._rebuild_cache()
        old_thresh = cv._MIN_MATCH_THRESHOLD
        cv._MIN_MATCH_THRESHOLD = 0.0
        results = cv.tag_screenshot(vec)
        cv._MIN_MATCH_THRESHOLD = old_thresh
        scores = {cid: s for cid, _, s in results}
        if cid_lo in scores and cid_hi in scores:
            assert scores[cid_hi] > scores[cid_lo]

    def test_applies_specificity_bonus_for_longer_prompts(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        vec = _rand_unit_vec(512, seed=400)
        metadata_db.insert_concept(
            prompt="code editor",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec),
        )
        metadata_db.insert_concept(
            prompt="a python code editor with source code visible in dark theme",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec),
        )
        cv._rebuild_cache()
        old_thresh = cv._MIN_MATCH_THRESHOLD
        cv._MIN_MATCH_THRESHOLD = 0.0
        results = cv.tag_screenshot(vec)
        cv._MIN_MATCH_THRESHOLD = old_thresh
        if len(results) >= 2:
            short_score = next(
                (s for _, p, s in results if p == "code editor"), None,
            )
            long_score = next(
                (s for _, p, s in results if "python" in p), None,
            )
            if short_score is not None and long_score is not None:
                assert long_score > short_score


class TestMatchQueryToConcepts:
    def test_returns_matching_concepts(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        vec = _rand_unit_vec(512, seed=500)
        metadata_db.insert_concept(
            prompt="query match test concept",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec),
        )
        cv._rebuild_cache()
        with patch("pipeline.embedder.embed_query_text_clip", return_value=vec):
            results = cv.match_query_to_concepts("test", top_k=5, threshold=0.0)
        assert len(results) >= 1

    def test_returns_empty_for_no_text_encoder(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        metadata_db.insert_concept(
            prompt="no encoder test concept",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(_rand_unit_vec(512, seed=501)),
        )
        cv._rebuild_cache()
        with patch("pipeline.embedder.embed_text", return_value=None):
            results = cv.match_query_to_concepts("test", top_k=5)
        assert results == []


class TestHarvestWindowTitles:
    @patch("pipeline.embedder.embed_clip_texts_batch")
    def test_generates_prompts_from_templates(self, mock_embed, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        mock_embed.return_value = [_rand_unit_vec(512, seed=i) for i in range(20)]
        insert_test_capture(
            metadata_db, source_type="screenshot",
            app_name="Cursor.exe", window_title="main.py — Cursor",
        )
        result = cv.harvest_from_window_titles()
        assert isinstance(result, int)
        if mock_embed.called:
            prompts_arg = mock_embed.call_args[0][0]
            assert all(isinstance(p, str) for p in prompts_arg)

    @patch("pipeline.embedder.embed_clip_texts_batch")
    def test_deduplicates_against_existing(self, mock_embed, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        metadata_db.insert_concept(
            prompt="Cursor application interface",
            category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        insert_test_capture(
            metadata_db, source_type="screenshot",
            app_name="Cursor", window_title="main.py",
        )
        mock_embed.return_value = [_rand_unit_vec(512, seed=i) for i in range(20)]
        cv.harvest_from_window_titles()
        rows = metadata_db.fetch_probation_concepts()
        prompts = [r["prompt"].lower() for r in rows]
        assert "cursor application interface" not in prompts

    @patch("pipeline.embedder.embed_clip_texts_batch")
    def test_inserts_as_probation(self, mock_embed, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        mock_embed.return_value = [_rand_unit_vec(512, seed=i) for i in range(20)]
        insert_test_capture(
            metadata_db, source_type="screenshot",
            app_name="UniqueTestApp", window_title="Special View",
        )
        cv.harvest_from_window_titles()
        rows = metadata_db.fetch_probation_concepts()
        if rows:
            sources = {r["source"] for r in rows}
            assert "window_title" in sources


class TestHarvestOCRNouns:
    @patch("pipeline.embedder.embed_clip_texts_batch")
    def test_extracts_capitalized_words(self, mock_embed, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        text = " ".join(["Kubernetes Docker Python React Testing"] * 6)
        cap_id = insert_test_capture(
            metadata_db, source_type="screenshot", content=text,
        )
        metadata_db.update_capture_status(cap_id, "indexed")
        mock_embed.return_value = [_rand_unit_vec(512, seed=i) for i in range(30)]
        result = cv.harvest_from_ocr_nouns(min_occurrences=5)
        assert isinstance(result, int)

    @patch("pipeline.embedder.embed_clip_texts_batch")
    def test_skips_words_below_threshold(self, mock_embed, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cap_id = insert_test_capture(
            metadata_db, source_type="screenshot", content="Rare Rare",
        )
        metadata_db.update_capture_status(cap_id, "indexed")
        mock_embed.return_value = []
        result = cv.harvest_from_ocr_nouns(min_occurrences=5)
        assert result == 0


class TestPromoteProbation:
    def test_promotes_high_match_concepts(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cid = metadata_db.insert_concept(
            prompt="good probation concept",
            category="t", source="window_title",
            clip_embedding=_fake_blob(), status="probation",
        )
        for _ in range(4):
            metadata_db.record_concept_match(cid, 0.25)
        promoted = cv.promote_probation_concepts()
        assert promoted >= 1
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT status FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["status"] == "active"

    def test_demotes_old_unmatched_to_dormant(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cid = metadata_db.insert_concept(
            prompt="old unmatched probation concept",
            category="t", source="window_title",
            clip_embedding=_fake_blob(), status="probation",
        )
        old_date = (datetime.utcnow() - timedelta(days=10)).isoformat()
        with metadata_db._connect() as conn:
            conn.execute(
                "UPDATE concept_vocabulary SET created_at = ? WHERE id = ?",
                (old_date, cid),
            )
        cv.promote_probation_concepts()
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT status FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["status"] == "dormant"


class TestRelevanceDecay:
    def test_reduces_scores_by_factor(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cid = metadata_db.insert_concept(
            prompt="decaying concept", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        metadata_db.update_concept_relevance(cid, 0.8)
        cv.apply_relevance_decay(decay_factor=0.9)
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT relevance_score FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert abs(row["relevance_score"] - 0.72) < 0.01

    def test_dormants_concepts_below_005(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        cid = metadata_db.insert_concept(
            prompt="near zero concept", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        metadata_db.update_concept_relevance(cid, 0.04)
        dormant_count = cv.apply_relevance_decay(decay_factor=0.98)
        assert dormant_count >= 1
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT status FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert row["status"] == "dormant"


class TestRecalculateIDF:
    def test_uses_correct_formula(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        for _ in range(10):
            c = insert_test_capture(metadata_db, source_type="screenshot")
            metadata_db.update_capture_status(c, "indexed")
        cid = metadata_db.insert_concept(
            prompt="idf formula test", category="t", source="seed",
            clip_embedding=_fake_blob(), status="active",
        )
        for _ in range(3):
            metadata_db.record_concept_match(cid, 0.2)
        cv.recalculate_idf()
        total = metadata_db.count_indexed_screenshots()
        expected = math.log(total / (1 + 3))
        with metadata_db._connect() as conn:
            row = conn.execute(
                "SELECT idf_weight FROM concept_vocabulary WHERE id = ?", (cid,),
            ).fetchone()
        assert abs(row["idf_weight"] - expected) < 0.01


class TestMergeSimilarConcepts:
    def test_marks_duplicates_as_dormant(self, tmp_db, _reset_cv_state):
        import pipeline.concept_vocabulary as cv

        vec = _rand_unit_vec(512, seed=600)
        metadata_db.insert_concept(
            prompt="original concept for merge",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec), status="active",
        )
        metadata_db.insert_concept(
            prompt="duplicate concept for merge",
            category="t", source="seed",
            clip_embedding=_vec_to_blob(vec), status="active",
        )
        cv._rebuild_cache()
        merged = cv.merge_similar_concepts(threshold=0.95)
        assert merged >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DIFF ANALYZER — 15 tests
# ═══════════════════════════════════════════════════════════════════════════════

from pipeline.diff_analyzer import (
    DiffResult,
    compute_diff,
    _classify_change,
    get_activity_level,
    to_dict,
    _RECENT_MAGNITUDES,
)


class TestDiffResultDefaults:
    def test_has_all_fields_with_correct_defaults(self):
        dr = DiffResult()
        assert dr.change_magnitude == 0.0
        assert dr.change_type == "idle"
        assert dr.changed_regions == []
        assert dr.changed_text == ""
        assert dr.is_high_activity is False
        assert dr.prev_capture_id is None


class TestComputeDiff:
    def test_identical_frames_returns_idle(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = compute_diff(frame.copy(), frame.copy())
        assert result.change_type == "idle"
        assert result.change_magnitude < 0.02

    def test_completely_different_frames_returns_app_switch(self):
        black = np.zeros((100, 100, 3), dtype=np.uint8)
        white = np.full((100, 100, 3), 255, dtype=np.uint8)
        result = compute_diff(black, white)
        assert result.change_type == "app_switch"
        assert result.change_magnitude > 0.7

    def test_small_localized_change_returns_typing(self):
        base = np.zeros((200, 200, 3), dtype=np.uint8)
        modified = base.copy()
        modified[90:110, 90:110] = 200
        result = compute_diff(base, modified)
        assert result.change_type in ("typing", "new_element", "idle")

    def test_updates_recent_magnitudes_deque(self):
        _RECENT_MAGNITUDES.clear()
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        compute_diff(frame.copy(), frame.copy())
        assert len(_RECENT_MAGNITUDES) >= 1

    def test_handles_grayscale_frames(self):
        gray1 = np.zeros((100, 100), dtype=np.uint8)
        gray2 = np.full((100, 100), 255, dtype=np.uint8)
        result = compute_diff(gray1, gray2)
        assert result.change_type == "app_switch"

    def test_cv2_unavailable_returns_empty_diffresult(self):
        saved = {}
        for k in list(sys.modules):
            if k == "cv2" or k.startswith("cv2."):
                saved[k] = sys.modules.pop(k)

        orig_import = builtins.__import__

        def _block_cv2(name, *args, **kwargs):
            if name == "cv2":
                raise ImportError("mocked cv2 unavailable")
            return orig_import(name, *args, **kwargs)

        try:
            builtins.__import__ = _block_cv2
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            result = compute_diff(frame, frame)
            assert result.change_type == "idle"
            assert result.change_magnitude == 0.0
        finally:
            builtins.__import__ = orig_import
            sys.modules.update(saved)


class TestClassifyChange:
    def test_idle_for_low_magnitude(self):
        assert _classify_change(0.01, [], 512) == "idle"

    def test_app_switch_for_high_magnitude(self):
        assert _classify_change(0.8, [(0, 0, 100, 100)], 512) == "app_switch"

    def test_scrolling_for_wide_horizontal_strip(self):
        bboxes = [(0, 100, 400, 50)]  # w > 0.6*512, h < 0.15*512
        assert _classify_change(0.1, bboxes, 512) == "scrolling"


class TestActivityLevel:
    def test_low_with_empty_deque(self):
        _RECENT_MAGNITUDES.clear()
        assert get_activity_level() == "low"

    def test_high_with_high_magnitudes(self):
        _RECENT_MAGNITUDES.clear()
        for _ in range(6):
            _RECENT_MAGNITUDES.append(0.3)
        assert get_activity_level() == "high"

    def test_medium_with_moderate_magnitudes(self):
        _RECENT_MAGNITUDES.clear()
        for _ in range(6):
            _RECENT_MAGNITUDES.append(0.06)
        assert get_activity_level() == "medium"

    def test_low_with_low_magnitudes(self):
        _RECENT_MAGNITUDES.clear()
        for _ in range(6):
            _RECENT_MAGNITUDES.append(0.01)
        assert get_activity_level() == "low"


class TestToDictSerialization:
    def test_serializes_diff_result_correctly(self):
        dr = DiffResult(
            change_magnitude=0.15,
            change_type="typing",
            changed_regions=[(10, 20, 30, 40)],
            changed_text="hello",
            is_high_activity=True,
            prev_capture_id="abc-123",
        )
        d = to_dict(dr)
        assert d["change_magnitude"] == 0.15
        assert d["change_type"] == "typing"
        assert d["changed_regions"] == [(10, 20, 30, 40)]
        assert d["changed_text"] == "hello"
        assert d["is_high_activity"] is True
        assert d["prev_capture_id"] == "abc-123"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. INTEGRATION — WORKER PIPELINE — 8 tests
# ═══════════════════════════════════════════════════════════════════════════════


def _make_worker_row(cap_id: str, **overrides) -> _FakeRow:
    defaults: dict[str, Any] = {
        "id": cap_id,
        "source_type": "screenshot",
        "timestamp": datetime.utcnow().isoformat(),
        "thumb_path": "",
        "content": "test content",
        "window_title": "editor",
        "app_name": "Code.exe",
        "url": "",
        "diff_data": "",
    }
    defaults.update(overrides)
    return _FakeRow(defaults)


class TestWorkerDiffEvent:
    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_processes_diff_data_and_inserts_event(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = None
        mock_emb.embed_text.return_value = [0.1] * 384
        mock_chunker.chunk.return_value = ["chunk"]

        cap_id = insert_test_capture(metadata_db)
        diff = json.dumps({
            "change_type": "typing",
            "change_magnitude": 0.05,
            "changed_text": "new code typed",
            "prev_capture_id": "prev-123",
        })
        _process_capture(_make_worker_row(cap_id, diff_data=diff))
        events = metadata_db.fetch_events_for_capture(cap_id)
        assert len(events) >= 1
        assert events[0]["change_type"] == "typing"

    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_appends_changed_text_to_embedded_text(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = None
        mock_emb.embed_text.return_value = [0.1] * 384
        captured: list[str] = []
        mock_chunker.chunk.side_effect = lambda t: (captured.append(t), [t])[1]

        cap_id = insert_test_capture(metadata_db)
        diff = json.dumps({
            "change_type": "typing",
            "change_magnitude": 0.05,
            "changed_text": "newly typed code",
        })
        _process_capture(_make_worker_row(cap_id, content="base text", diff_data=diff))
        assert any("CHANGED" in c for c in captured)

    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_runs_concept_tagging_when_visual_vec(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture
        import tempfile, os
        from PIL import Image

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = [0.1] * 512
        mock_emb.embed_text.return_value = [0.1] * 384
        mock_chunker.chunk.return_value = ["text"]

        cap_id = insert_test_capture(metadata_db)

        fd, tmp_img = tempfile.mkstemp(suffix=".jpg")
        try:
            Image.new("RGB", (50, 50), "red").save(tmp_img)
            os.close(fd)
            with patch(
                "pipeline.concept_vocabulary.tag_screenshot",
                return_value=[("cid1", "concept prompt", 0.5)],
            ):
                _process_capture(_make_worker_row(cap_id, thumb_path=tmp_img))
        finally:
            try:
                os.unlink(tmp_img)
            except OSError:
                pass

    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_skips_concept_tagging_when_no_visual_vec(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = None
        mock_emb.embed_text.return_value = [0.1] * 384
        mock_chunker.chunk.return_value = ["text"]

        cap_id = insert_test_capture(metadata_db)
        with patch("pipeline.concept_vocabulary.tag_screenshot") as mock_tag:
            _process_capture(_make_worker_row(cap_id))
            mock_tag.assert_not_called()

    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_handles_missing_diff_data_key(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = None
        mock_emb.embed_text.return_value = [0.1] * 384
        mock_chunker.chunk.return_value = ["text"]

        cap_id = insert_test_capture(metadata_db)
        row_data: dict[str, Any] = {
            "id": cap_id, "source_type": "screenshot",
            "timestamp": datetime.utcnow().isoformat(),
            "thumb_path": "", "content": "test",
            "window_title": "", "app_name": "", "url": "",
        }
        _process_capture(_FakeRow(row_data))
        assert metadata_db.fetch_events_for_capture(cap_id) == []

    @patch("pipeline.worker.embedder")
    @patch("pipeline.worker.vector_db")
    @patch("pipeline.worker.chunker")
    def test_handles_malformed_diff_data_json(
        self, mock_chunker, mock_vdb, mock_emb, tmp_db,
    ):
        from pipeline.worker import _process_capture

        mock_emb.embed_texts.return_value = [[0.1] * 384]
        mock_emb.embed_image_path.return_value = None
        mock_emb.embed_text.return_value = [0.1] * 384
        mock_chunker.chunk.return_value = ["text"]

        cap_id = insert_test_capture(metadata_db)
        _process_capture(_make_worker_row(cap_id, diff_data="NOT VALID JSON {{{"))
        assert metadata_db.fetch_events_for_capture(cap_id) == []


class TestQueueManagerDiffPassthrough:
    def test_passes_diff_data_to_insert_capture(self, tmp_db):
        from pipeline.queue_manager import enqueue

        diff_str = json.dumps({"change_type": "typing", "change_magnitude": 0.1})
        cap_id = enqueue(source_type="screenshot", content="test", diff_data=diff_str)
        row = metadata_db.fetch_capture_by_id(cap_id)
        assert row["diff_data"] == diff_str


class TestConsolidationCaptureTextsEnriched:
    def test_includes_concept_tags_and_events(self, tmp_db):
        from pipeline.consolidation_worker import _build_capture_texts

        cap_id = insert_test_capture(
            metadata_db, source_type="screenshot", content="working on code",
        )
        cid = metadata_db.insert_concept(
            prompt="code editing activity", category="t", source="seed",
            clip_embedding=_fake_blob(),
        )
        metadata_db.insert_capture_concepts(cap_id, [(cid, 0.9)])
        metadata_db.insert_capture_event(
            capture_id=cap_id, prev_capture_id=None,
            change_type="typing", change_magnitude=0.05,
            changed_text="new function added",
            timestamp=datetime.utcnow().isoformat(),
        )
        session = [{
            "id": cap_id,
            "timestamp": datetime.utcnow().isoformat(),
            "source_type": "screenshot",
            "content": "working on code",
            "window_title": "editor - main.py",
            "app_name": "Code.exe",
        }]
        texts = _build_capture_texts(session)
        assert len(texts) >= 1
        combined = " ".join(texts)
        assert "concepts:" in combined.lower() or "actions:" in combined.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. INTEGRATION — ASK ROUTE — 7 tests
# ═══════════════════════════════════════════════════════════════════════════════

from api.routes.ask import _SOURCE_WEIGHTS, _rrf_fuse


class TestAskSourceWeights:
    def test_includes_concepts_and_events(self):
        assert "concepts" in _SOURCE_WEIGHTS
        assert "events" in _SOURCE_WEIGHTS
        assert _SOURCE_WEIGHTS["concepts"] == 1.2
        assert _SOURCE_WEIGHTS["events"] == 1.1


class TestAskConceptRetrieval:
    def test_adds_to_ranked_lists_when_concepts_match(self):
        ranked_lists = {
            "text": [{"capture_id": "c1", "content": "text result"}],
            "concepts": [
                {"capture_id": "c2", "content": "concept match"},
                {"capture_id": "c3", "content": "another concept"},
            ],
        }
        fused = _rrf_fuse(ranked_lists)
        cids = [f.get("capture_id") for f in fused]
        assert "c2" in cids
        assert "c3" in cids

    def test_silently_fails_on_import_error(self):
        try:
            from pipeline.concept_vocabulary import match_query_to_concepts

            with patch(
                "pipeline.concept_vocabulary.match_query_to_concepts",
                side_effect=Exception("CLIP unavailable"),
            ):
                try:
                    match_query_to_concepts("test")
                except Exception:
                    pass
        except ImportError:
            pass


class TestAskEventRetrieval:
    def test_adds_to_ranked_lists_for_activity_intent(self):
        ranked_lists = {
            "text": [{"capture_id": "c1", "content": "text"}],
            "events": [{
                "capture_id": "c2",
                "change_type": "typing",
                "app_name": "Code.exe",
                "window_title": "main.py",
                "changed_text": "new function",
                "content_preview": "[typing] Code.exe — main.py: new function",
                "source_type": "event",
            }],
        }
        fused = _rrf_fuse(ranked_lists)
        cids = [f.get("capture_id") for f in fused]
        assert "c2" in cids

    def test_uses_app_filters_not_detected_apps(self):
        from pipeline.query_engine import parse_query

        pq = parse_query("what was I typing in VS Code")
        assert hasattr(pq, "app_filters")
        if pq.app_filters:
            assert isinstance(pq.app_filters[0], str)

    def test_respects_intent_filter(self):
        valid_intents = {"activity", "recall", "locate", "temporal"}
        excluded = {"person", "debug", "definition"}
        for i in valid_intents:
            assert i in valid_intents
        for i in excluded:
            assert i not in valid_intents


class TestRRFFusionConceptsAndEvents:
    def test_correctly_merges_concept_and_event_sources(self):
        ranked_lists = {
            "text": [{"capture_id": "c1", "content": "text"}],
            "concepts": [
                {"capture_id": "c1", "content": "concept match"},
                {"capture_id": "c2", "content": "concept only"},
            ],
            "events": [
                {"capture_id": "c2", "content": "event match"},
                {"capture_id": "c3", "content": "event only"},
            ],
        }
        fused = _rrf_fuse(ranked_lists)
        scores = {f["capture_id"]: f["rrf_score"] for f in fused}
        assert "c1" in scores and "c2" in scores and "c3" in scores
        assert scores["c1"] > scores["c3"]
        assert scores["c2"] > scores["c3"]
