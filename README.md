# Engram

> In neuroscience, an engram is the physical trace a memory
> leaves in your brain — the cellular change that *is* the memory.
> Every experience rewires something. Nothing is truly forgotten.
>
> Your computer has been living a rich life.
> You've been forgetting all of it.
>
> Engram remembers.

---

## What Is This?

Your brain has a hippocampus.
It encodes every experience into long-term memory,
links moments by context, and lets you reconstruct
the past from a single fragment of association.

Your computer has never had one.

Until now.

**Engram** is a passive, local-first lifelogging engine that captures
your digital life — screenshots, clipboard, browser history, documents,
audio — embeds it semantically, and lets you search it by meaning.

Not by filename. Not by date. By *what it was about.*

---

## How It Works

```
Your screen, your words, your audio
         ↓
    Captured silently in the background
         ↓
    OCR'd and understood by EasyOCR
         ↓
    Embedded as meaning — not text — via sentence-transformers + CLIP
         ↓
    Stored in a local vector database (ChromaDB)
         ↓
    Retrieved by what you remember about it,
    not what it was called
```

---

## The Brain Analogy

| Your Brain | Engram |
|---|---|
| Hippocampus encodes moments | Collectors capture events every 30s |
| Sleep consolidates into long-term storage | Background worker embeds asynchronously |
| Neocortex stores permanent memories | ChromaDB vector store |
| Recall via association | Semantic similarity search |
| Amygdala flags emotional importance | Relevance score + recency weighting |
| Context reconstruction from fragments | Temporal context window ±5 minutes |
| Different regions for different memory types | Dual embeddings: text (MiniLM) + visual (CLIP) |

The brain never stores files.
It stores patterns, relationships, and meaning.
So does Engram.

---

## Architecture

```
HOT PATH  (real-time, < 50ms)
  Collectors → perceptual hash dedupe → thumbnail → SQLite job_queue

COLD PATH  (async, every 2 minutes)
  Worker → EasyOCR → chunk → embed (text + CLIP) → ChromaDB

QUERY PATH  (user triggered)
  Search query → dual vector search → cross-encoder rerank → results

FRONTEND
  React 18 + TypeScript → FastAPI (localhost:8765) → ChromaDB
```

---

## Data Sources

- **Screenshots** — every 30 seconds, perceptual-hash deduplicated
- **Clipboard** — on every copy event, change-detected
- **Browser History** — Chrome and Firefox SQLite databases
- **Active Window** — title and application name per capture
- **File System** — document opens and edits via filesystem watcher
- **Audio** — ambient recording → Whisper transcription (optional)

---

## Privacy

100% local. Zero cloud. Nothing leaves your machine. Ever.

This is the privacy-first, open-source alternative to Microsoft Recall —
built in the open, owned by you, running entirely on your hardware.

No API keys. No accounts. No telemetry.

---

## Quick Start

```bash
git clone https://github.com/MrRaccooon/Engram
cd Engram

# Backend
pip install -r requirements.txt
python main.py          # starts daemon + API + system tray

# Frontend (separate terminal)
cd frontend
npm install
npm run dev             # opens at http://localhost:5173
```

Open [http://localhost:5173](http://localhost:5173) and start remembering.

> **Windows startup:** Run `python scripts/install_windows.py` once to register Engram
> as a Task Scheduler job that starts automatically on login.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+K` | Jump to search |
| `Ctrl+T` | Open timeline |
| `Ctrl+Shift+M` | Capture right now (global hotkey) |
| `Esc` | Close detail modal |

---

## Troubleshooting

**OCR is slow on first run** — EasyOCR downloads its model (~100MB) on first use. Subsequent runs are instant.

**Chrome history not found** — Close Chrome before starting Engram (Chrome locks its History DB while open).

**Clipboard not capturing** — Requires `pywin32`. Run `pip install pywin32` and then `python -m pywin32_postinstall -install`.

**System tray not appearing** — Run `pip install pystray pillow` and restart.

**Search returns no results** — The cold-path worker runs every 2 minutes. Wait for the queue to drain (visible in Settings → Status).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Collectors | pynput, Pillow, win32clipboard, pywin32, watchdog |
| OCR | EasyOCR |
| Text Embedding | sentence-transformers (all-MiniLM-L6-v2) |
| Visual Embedding | open-clip-torch (ViT-B/32) |
| Reranking | cross-encoder (ms-marco-MiniLM-L-6-v2) |
| Vector DB | ChromaDB (local, embedded) |
| Metadata DB | SQLite (stdlib) |
| API | FastAPI + Pydantic v2 |
| Scheduling | APScheduler |
| Frontend | React 18 + TypeScript + Vite + TailwindCSS + shadcn/ui |
| System Tray | pystray |

---

*"The existence of forgetting has never been proved.
We only know that some things don't come to mind when we want them."*
— Friedrich Nietzsche
