"""
God-level semantic quality tests.

These tests verify that the system ACTUALLY UNDERSTANDS content,
not just that functions return values. They use real CLIP, real MiniLM,
and real cv2 to check semantic correctness.

Skip if CLIP/models unavailable (CI without GPU).
"""
from __future__ import annotations

import math
import os
import sys
import struct
import tempfile
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Skip if models can't load ──────────────────────────────────────────────
_MODELS_AVAILABLE = True
try:
    from pipeline import embedder
    _test_vec = embedder.embed_query_text_clip("test")
    if _test_vec is None or abs(np.linalg.norm(_test_vec) - 1.0) > 0.1:
        _MODELS_AVAILABLE = False
except Exception:
    _MODELS_AVAILABLE = False

skip_no_models = pytest.mark.skipif(
    not _MODELS_AVAILABLE,
    reason="CLIP model not available or not producing valid embeddings"
)


def _make_image(width=400, height=300, color=(0, 0, 0)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


def _add_text(img, text, x=20, y=20, color=(255, 255, 255)):
    draw = ImageDraw.Draw(img)
    draw.text((x, y), text, fill=color)
    return img


def _save_tmp(img) -> str:
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img.save(path)
    return path


def _clip_sim(path: str, text: str) -> float:
    """Cosine similarity between an image and a text prompt via CLIP."""
    ivec = embedder.embed_image_path(path)
    tvec = embedder.embed_query_text_clip(text)
    if not ivec or not tvec:
        return 0.0
    return float(np.dot(ivec, tvec))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CLIP SANITY — Does CLIP produce meaningful, differentiated embeddings?
# ═══════════════════════════════════════════════════════════════════════════════

@skip_no_models
class TestCLIPSanity:
    """Verify CLIP vectors are real pretrained weights, not random."""

    def test_vectors_are_normalized(self):
        vec = embedder.embed_query_text_clip("a photo of a cat")
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_image_vectors_normalized(self):
        img = _make_image(color=(100, 150, 200))
        path = _save_tmp(img)
        vec = embedder.embed_image_path(path)
        os.unlink(path)
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_different_texts_produce_different_vectors(self):
        v1 = embedder.embed_query_text_clip("a photo of a dog")
        v2 = embedder.embed_query_text_clip("a screenshot of python code")
        sim = float(np.dot(v1, v2))
        assert sim < 0.95, f"Different concepts should not be near-identical: {sim}"

    def test_similar_texts_produce_similar_vectors(self):
        v1 = embedder.embed_query_text_clip("a screenshot of a code editor")
        v2 = embedder.embed_query_text_clip("a screenshot of an IDE with source code")
        sim = float(np.dot(v1, v2))
        assert sim > 0.70, f"Similar concepts should be similar: {sim}"

    def test_batch_matches_individual(self):
        texts = ["hello world", "a photo of a sunset"]
        batch = embedder.embed_clip_texts_batch(texts)
        single0 = embedder.embed_query_text_clip(texts[0])
        sim = float(np.dot(batch[0], single0))
        assert sim > 0.99, f"Batch and individual should match: {sim}"

    def test_different_images_produce_different_vectors(self):
        red = _make_image(color=(255, 0, 0))
        blue = _make_image(color=(0, 0, 255))
        p1, p2 = _save_tmp(red), _save_tmp(blue)
        v1 = embedder.embed_image_path(p1)
        v2 = embedder.embed_image_path(p2)
        os.unlink(p1)
        os.unlink(p2)
        sim = float(np.dot(v1, v2))
        assert sim < 0.99, f"Different images should differ: {sim}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONCEPT TAGGING QUALITY — Do the right concepts match the right images?
# ═══════════════════════════════════════════════════════════════════════════════

@skip_no_models
class TestConceptTaggingQuality:
    """Verify that concept tagging produces semantically correct results."""

    @pytest.fixture(autouse=True)
    def setup_concepts(self, tmp_path):
        from storage import metadata_db
        metadata_db.init(tmp_path / "quality.db")
        import pipeline.concept_vocabulary as cv
        cv._initialized = False
        cv.init()
        yield
        metadata_db._DB_PATH = None
        cv._initialized = False
        cv._concept_matrix = None
        cv._concept_ids = []
        cv._concept_prompts = []

    def test_dark_image_not_tagged_as_cat(self):
        """A dark screenshot should not match 'photo of a cat'."""
        import pipeline.concept_vocabulary as cv
        dark = _make_image(color=(20, 20, 30))
        path = _save_tmp(dark)
        vec = embedder.embed_image_path(path)
        os.unlink(path)

        tags = cv.tag_screenshot(vec)
        tag_prompts = [p.lower() for _, p, _ in tags]
        assert not any("cat" in p or "pet" in p for p in tag_prompts), \
            f"Dark image incorrectly tagged with animal concepts: {tag_prompts}"

    def test_concept_scores_are_reasonable_range(self):
        """Concept scores should be in a reasonable range, not all zeros or all ones."""
        import pipeline.concept_vocabulary as cv
        img = _make_image(color=(50, 100, 200))
        _add_text(img, "def hello():\n    print('world')")
        path = _save_tmp(img)
        vec = embedder.embed_image_path(path)
        os.unlink(path)

        if cv._concept_matrix is not None:
            raw = cv._concept_matrix @ np.array(vec, dtype=np.float32)
            max_score = float(raw.max())
            min_score = float(raw.min())
            assert max_score > 0.05, f"Max concept score too low: {max_score}"
            assert max_score < 0.95, f"Max concept score suspiciously high: {max_score}"
            assert max_score != min_score, "All concepts have identical scores — model broken"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. QUERY MATCHING QUALITY — Do queries find the right concepts?
# ═══════════════════════════════════════════════════════════════════════════════

@skip_no_models
class TestQueryMatchingQuality:
    """Verify MiniLM-based query->concept matching is semantically correct."""

    @pytest.fixture(autouse=True)
    def setup_concepts(self, tmp_path):
        from storage import metadata_db
        metadata_db.init(tmp_path / "query_quality.db")
        import pipeline.concept_vocabulary as cv
        cv._initialized = False
        cv.init()
        yield
        metadata_db._DB_PATH = None
        cv._initialized = False
        cv._concept_matrix = None
        cv._concept_ids = []
        cv._concept_prompts = []

    def test_code_query_matches_code_concepts(self):
        """'What code was I writing' should match code-related concepts."""
        import pipeline.concept_vocabulary as cv
        matches = cv.match_query_to_concepts("what code was I writing", top_k=5, threshold=0.30)
        prompts = [p.lower() for _, p, _ in matches]
        assert any("code" in p or "editor" in p or "ide" in p for p in prompts), \
            f"Code query didn't match code concepts: {prompts}"

    def test_terminal_query_matches_terminal_concepts(self):
        """'Show me terminal output' should match terminal concepts."""
        import pipeline.concept_vocabulary as cv
        matches = cv.match_query_to_concepts("show me terminal output", top_k=5, threshold=0.30)
        prompts = [p.lower() for _, p, _ in matches]
        assert any("terminal" in p or "command" in p or "shell" in p for p in prompts), \
            f"Terminal query didn't match terminal concepts: {prompts}"

    def test_chat_query_matches_communication_concepts(self):
        """'Was I chatting with someone' should match chat concepts."""
        import pipeline.concept_vocabulary as cv
        matches = cv.match_query_to_concepts("was I chatting with someone", top_k=5, threshold=0.30)
        prompts = [p.lower() for _, p, _ in matches]
        assert any("chat" in p or "message" in p or "conversation" in p for p in prompts), \
            f"Chat query didn't match communication concepts: {prompts}"

    def test_error_query_matches_error_concepts(self):
        """'What errors did I see' should match error concepts."""
        import pipeline.concept_vocabulary as cv
        matches = cv.match_query_to_concepts("what errors did I encounter", top_k=5, threshold=0.30)
        prompts = [p.lower() for _, p, _ in matches]
        assert any("error" in p or "fail" in p or "crash" in p for p in prompts), \
            f"Error query didn't match error concepts: {prompts}"

    def test_unrelated_query_has_lower_scores(self):
        """An unrelated query should have lower max score than a relevant one."""
        import pipeline.concept_vocabulary as cv
        relevant = cv.match_query_to_concepts("show me the code editor", top_k=1, threshold=0.0)
        unrelated = cv.match_query_to_concepts("underwater basket weaving championship", top_k=1, threshold=0.0)
        if relevant and unrelated:
            assert relevant[0][2] > unrelated[0][2], \
                f"Relevant ({relevant[0][2]:.3f}) should score higher than unrelated ({unrelated[0][2]:.3f})"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DIFF ANALYZER QUALITY — Does it actually detect real changes?
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiffAnalyzerQuality:
    """Verify diff analysis produces correct action classifications."""

    def test_no_change_is_idle(self):
        from pipeline.diff_analyzer import compute_diff
        frame = np.random.randint(0, 255, (300, 400, 3), dtype=np.uint8)
        result = compute_diff(frame, frame.copy())
        assert result.change_type == "idle"
        assert result.change_magnitude < 0.02

    def test_complete_change_is_app_switch(self):
        from pipeline.diff_analyzer import compute_diff
        f1 = np.random.randint(0, 50, (300, 400, 3), dtype=np.uint8)
        f2 = np.random.randint(200, 255, (300, 400, 3), dtype=np.uint8)
        result = compute_diff(f1, f2)
        assert result.change_type == "app_switch"
        assert result.change_magnitude > 0.5

    def test_small_center_change_is_typing(self):
        from pipeline.diff_analyzer import compute_diff
        f1 = np.zeros((400, 600, 3), dtype=np.uint8)
        f2 = f1.copy()
        f2[140:260, 200:400] = 200
        result = compute_diff(f1, f2)
        assert result.change_type in ("typing", "new_element"), \
            f"Small center change should be typing/new_element, got {result.change_type} (mag={result.change_magnitude:.3f})"

    def test_horizontal_strip_is_scrolling(self):
        from pipeline.diff_analyzer import compute_diff
        f1 = np.zeros((400, 600, 3), dtype=np.uint8)
        f2 = f1.copy()
        f2[150:180, 20:580] = 180
        result = compute_diff(f1, f2)
        assert result.change_type == "scrolling", \
            f"Wide horizontal change should be scrolling, got {result.change_type}"

    def test_magnitude_proportional_to_change_size(self):
        from pipeline.diff_analyzer import compute_diff
        base = np.zeros((300, 300, 3), dtype=np.uint8)

        small_change = base.copy()
        small_change[140:160, 140:160] = 255

        big_change = base.copy()
        big_change[50:250, 50:250] = 255

        r_small = compute_diff(base, small_change)
        r_big = compute_diff(base, big_change)
        assert r_big.change_magnitude > r_small.change_magnitude, \
            f"Bigger change ({r_big.change_magnitude:.3f}) should have higher magnitude than smaller ({r_small.change_magnitude:.3f})"

    def test_activity_level_reflects_recent_history(self):
        from pipeline.diff_analyzer import compute_diff, get_activity_level, _RECENT_MAGNITUDES
        _RECENT_MAGNITUDES.clear()

        base = np.zeros((200, 200, 3), dtype=np.uint8)
        active = base.copy()
        active[50:150, 50:150] = 200
        for _ in range(4):
            compute_diff(base, active)

        assert get_activity_level() in ("high", "medium"), \
            f"Multiple active diffs should indicate high/medium activity, got {get_activity_level()}"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SCREENSHOT ANALYZER QUALITY — Does it classify apps correctly?
# ═══════════════════════════════════════════════════════════════════════════════

class TestScreenshotAnalyzerQuality:
    """Verify app detection and content type classification."""

    def test_cursor_detected_as_code(self):
        from pipeline.screenshot_analyzer import analyze
        ctx = analyze("some random text", app_name="cursor.exe")
        assert ctx.content_type == "code", f"Cursor should be code, got {ctx.content_type}"

    def test_vscode_detected_as_code(self):
        from pipeline.screenshot_analyzer import analyze
        ctx = analyze("some text", app_name="Code.exe")
        assert ctx.content_type == "code", f"VS Code should be code, got {ctx.content_type}"

    def test_chrome_detected_as_browser(self):
        from pipeline.screenshot_analyzer import analyze
        ctx = analyze("some text", app_name="chrome.exe")
        assert ctx.content_type == "browser"

    def test_terminal_detected_as_terminal(self):
        from pipeline.screenshot_analyzer import analyze
        ctx = analyze("$ git status", app_name="WindowsTerminal.exe")
        assert ctx.content_type == "terminal"

    def test_whatsapp_detected_correctly(self):
        from pipeline.screenshot_analyzer import analyze
        ctx = analyze("Hey how are you", app_name="WhatsApp.exe")
        assert ctx.content_type == "browser"

    def test_code_with_terminal_is_mixed(self):
        from pipeline.screenshot_analyzer import analyze
        text = """def main():
    pass

$ pytest tests/ -v
PASSED test_one
FAILED test_two
Error: AssertionError"""
        ctx = analyze(text, app_name="cursor.exe")
        assert ctx.content_type == "mixed", f"IDE with terminal output should be mixed, got {ctx.content_type}"

    def test_python_language_detected(self):
        from pipeline.screenshot_analyzer import analyze
        text = """import os
from pathlib import Path

def process_file(path: str) -> None:
    if __name__ == '__main__':
        self.run()"""
        ctx = analyze(text, app_name="cursor.exe")
        assert ctx.language == "python", f"Python code should detect python, got {ctx.language}"

    def test_functions_extracted_from_code(self):
        from pipeline.screenshot_analyzer import analyze
        text = """def parse_query(text):
    pass

def build_response(data):
    return data

class QueryEngine:
    pass"""
        ctx = analyze(text, app_name="Code.exe")
        assert "parse_query" in ctx.functions, f"Should extract parse_query, got {ctx.functions}"
        assert "build_response" in ctx.functions, f"Should extract build_response, got {ctx.functions}"

    def test_errors_extracted_from_terminal(self):
        from pipeline.screenshot_analyzer import analyze
        text = """$ python main.py
Traceback (most recent call last):
  File "main.py", line 42
TypeError: expected str got int
Error: Process exited with code 1"""
        ctx = analyze(text, app_name="WindowsTerminal.exe")
        assert len(ctx.errors) > 0, f"Should extract errors, got {ctx.errors}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. END-TO-END SEMANTIC PIPELINE — Does it all work together?
# ═══════════════════════════════════════════════════════════════════════════════

@skip_no_models
class TestEndToEndSemantic:
    """Verify the full pipeline produces semantically correct results."""

    @pytest.fixture(autouse=True)
    def setup_full(self, tmp_path):
        from storage import metadata_db, vector_db
        metadata_db.init(tmp_path / "e2e.db")
        vector_db.init(tmp_path / "chromadb")
        import pipeline.concept_vocabulary as cv
        cv._initialized = False
        cv.init()
        yield
        metadata_db._DB_PATH = None
        cv._initialized = False
        cv._concept_matrix = None
        cv._concept_ids = []
        cv._concept_prompts = []

    def test_retrieval_finds_code_capture_for_code_query(self):
        """If we index a screenshot with code-like content, a code query should find it."""
        from storage import metadata_db, vector_db
        from pipeline import embedder

        cap_id = metadata_db.insert_capture(
            source_type="screenshot",
            content="def parse_query(text): return ParsedQuery(raw=text) | file:query_engine.py | project:Engram",
            window_title="query_engine.py - Engram - Cursor",
            app_name="cursor.exe",
        )
        metadata_db.update_capture_status(cap_id, "indexed")

        content = "def parse_query(text): return ParsedQuery(raw=text) | file:query_engine.py | project:Engram"
        vec = embedder.embed_text(content)
        vector_db.upsert_text(
            doc_id=f"{cap_id}_t0", embedding=vec,
            content_preview=content[:300], capture_id=cap_id,
            timestamp="2026-05-03T15:00:00", source_type="screenshot",
            window_title="query_engine.py", app_name="cursor.exe",
        )

        query_vec = embedder.embed_text("what code was I writing in the query engine")
        results = vector_db.query_text(query_vec, top_k=3)
        assert len(results) >= 1, "Should find the code capture"
        assert results[0]["capture_id"] == cap_id, "Should find the right capture"

    def test_concept_tags_stored_and_retrievable(self):
        """Concepts tagged on a capture should be fetchable."""
        from storage import metadata_db
        import pipeline.concept_vocabulary as cv

        img = _make_image(color=(30, 30, 50))
        _add_text(img, "import numpy\ndef process():\n  return data")
        path = _save_tmp(img)
        vec = embedder.embed_image_path(path)
        os.unlink(path)

        cap_id = metadata_db.insert_capture(
            source_type="screenshot", content="code content",
            window_title="test.py", app_name="cursor.exe",
        )

        if vec:
            tags = cv.tag_screenshot(vec)
            if tags:
                metadata_db.insert_capture_concepts(
                    cap_id, [(cid, conf) for cid, _, conf in tags]
                )
                stored = metadata_db.fetch_concepts_for_capture(cap_id)
                assert len(stored) == len(tags), \
                    f"Should store all {len(tags)} tags, got {len(stored)}"

    def test_minilm_query_semantics(self):
        """MiniLM text similarity should be semantically meaningful."""
        v_code = embedder.embed_text("I was writing python code in VS Code")
        v_chat = embedder.embed_text("I was chatting with my friend on WhatsApp")
        v_query = embedder.embed_text("what programming was I doing")

        sim_code = float(np.dot(v_query, v_code))
        sim_chat = float(np.dot(v_query, v_chat))

        assert sim_code > sim_chat, \
            f"'programming' query should be more similar to 'writing code' ({sim_code:.3f}) than 'chatting' ({sim_chat:.3f})"
