---
name: pdf-reading
description: Read, chunk, index, retrieve, and page-cite long PDFs (100–1000+ pages). Use this when asked to summarize, extract facts, or answer questions about PDFs while preserving traceability and accuracy.
license: MIT
---

# PDF Reading Skill

This skill equips the agent to **extract**, **chunk**, **index**, **retrieve**, and **cite** content from **very long PDFs** while keeping processing scalable and results **page-cited**. It is offline (local TF‑IDF), robust to large documents, and saves artifacts for traceability.

---

## When to use this skill

Use **/pdf-reading** when you need to:
- Read or summarize long PDFs (100+ pages)
- Extract facts, deadlines, requirements, or procedures with **page citations**
- Build a **quick outline** of a PDF before deep reading
- Answer targeted questions using only the **most relevant slices** (top‑K retrieval)
- Work efficiently without overflowing the context window

---

## What this skill provides

**Scripts** (under `scripts/`):
- `pdf_to_chunks.py` – Extracts text, creates overlapping chunks with page metadata  
- `select_chunks.py` – Retrieves **top‑K** relevant chunks for a natural‑language query (TF‑IDF cosine)  
- `show_chunks.py` – Outlines and previews chunks; filters by IDs, page ranges, regex; export to JSONL/Markdown

**Artifacts** (under your output directory):
- `manifest.json` – Source, parameters, page count, chunk count, scan-heuristic, outline flag
- `chunks.jsonl` – One JSON object per chunk: `chunk_id`, `pages`, `heading_path`, `text`
- `logs/*.log` – Human-readable run logs

---

## Prerequisites

- Python 3.9+
- `pypdf2` (for `PyPDF2.PdfReader`)  
  Install if needed: `pip install pypdf2`
- Optional (for scanned PDFs): `ocrmypdf` CLI. If text extraction is very low, run OCR and re‑extract.

---

## Output paths, logs, and traceability

Default artifact base:
```

${CHANGE\_ID\_BASE:-./agent\_artifacts}/pdf-reading/${CHANGE\_ID:-local}/

```

**If your environment supports it, mirror to:**
```

/Users/mckerracher.joshua/Documents/sbx-rls-iac-josh/Work/Orchestrated-agent-work/{CHANGE-ID}/

````

> **Agent rule:** If `CHANGE_ID` is defined, **always** place outputs under the matching `{CHANGE-ID}` path for story‑level traceability. If not defined, fall back to `./agent_artifacts/`.

Artifacts to always write:
- `manifest.json`
- `chunks.jsonl`
- `logs/pdf_to_chunks.log`

---

## Quick Start

Given a PDF at `docs/source.pdf`:

```bash
# 0) Prepare traceable output location
export CHANGE_ID="STORY-1234"
export CHANGE_ID_BASE="/Users/mckerracher.joshua/Documents/sbx-rls-iac-josh/Work/Orchestrated-agent-work"
OUT="${CHANGE_ID_BASE}/Orchestrated-agent-work/${CHANGE_ID}/pdf-reading"
mkdir -p "$OUT/logs"

# 1) Extract and chunk
python skills/pdf-reading/scripts/pdf_to_chunks.py \
  --pdf docs/source.pdf \
  --out-dir "$OUT" \
  --approx-chars-per-chunk 5000 \
  --overlap-chars 600

# 2) Inspect outline and sample chunks
python skills/pdf-reading/scripts/show_chunks.py \
  --chunks "$OUT/chunks.jsonl" \
  --outline \
  --head 3

# 3) Retrieve relevant chunks for a question
python skills/pdf-reading/scripts/select_chunks.py \
  --chunks "$OUT/chunks.jsonl" \
  --query "List security requirements and their deadlines" \
  --top-k 12 \
  > "$OUT/selected.jsonl"

# 4) Draft answers using ONLY $OUT/selected.jsonl, with page citations (p./pp.)
````

***

## Agent Process

1.  **Preparation**
    *   Run `pdf_to_chunks.py`. If `manifest.scanned_pdf_hint == true` or chunks look empty/garbled, suggest:
        ```bash
        ocrmypdf --deskew --optimize 1 input.pdf ocr.pdf
        ```
        Re-run extraction on `ocr.pdf`.

2.  **Chunking parameters (large PDFs)**
    *   `--approx-chars-per-chunk`: **4000–8000**
    *   `--overlap-chars`: **10–15%** of chunk size (e.g., 400–1200)

3.  **Outline & sanity-check**
    *   `show_chunks.py --outline --head 5` to confirm structure and sample content.

4.  **Retrieval**
    *   `select_chunks.py --top-k 8–16` for focused Q\&A (expand as needed).
    *   Only read selected chunks to keep context tight.

5.  **Answer with citations**
    *   Cite pages for every factual claim: `(p. 47)` or `(pp. 42–45)`.
    *   If certainty is low (ambiguous text, conflicting passages), state limits and suggest more slices.

6.  **Map–Reduce for very long docs**
    *   Pass 1: Summarize chunks per section/heading.
    *   Pass 2: Synthesize a consolidated answer.
    *   Save interim summaries under the artifact directory.

***

## Command Recipes

*   **Create a two‑level outline and preview:**
    ```bash
    python scripts/show_chunks.py --chunks "$OUT/chunks.jsonl" --outline --head 5
    ```

*   **Filter chunks by page range & grep:**
    ```bash
    python scripts/show_chunks.py \
      --chunks "$OUT/chunks.jsonl" \
      --pages "40-75" \
      --grep "(deadline|due\s+date|milestone)s?" \
      --head 10
    ```

*   **Export selected chunks to Markdown:**
    ```bash
    python scripts/show_chunks.py \
      --chunks "$OUT/chunks.jsonl" \
      --chunk-id c00012 c00013 c00014 \
      --markdown \
      --export "$OUT/selected.md"
    ```

*   **Top‑K retrieve then preview those chunks:**
    ```bash
    python scripts/select_chunks.py \
      --chunks "$OUT/chunks.jsonl" \
      --query "key compliance milestones" \
      --top-k 12 > "$OUT/selected.jsonl"

    # Preview selection in Markdown
    jq -r '."\u0000"' >/dev/null 2>&1 || true # (no-op placeholder to avoid jq dependency)
    python scripts/show_chunks.py \
      --chunks "$OUT/selected.jsonl" \
      --markdown \
      --head 12
    ```

***

## Response Quality Rules

*   **Always page‑cite** claims derived from the PDF.
*   If tables are essential but extraction is poor, recommend table export (e.g., Tabula) or manual CSV for those pages.
*   Be crisp; add a **Confidence & Gaps** note when appropriate.

***

## Troubleshooting

*   **Empty or garbled**: PDF likely scanned → OCR → re-run.
*   **No headings**: Many PDFs lack bookmarks; outline will be synthetic but chunking works.
*   **Performance**: Increase `approx-chars-per-chunk`; reduce `top-k` initially; iterate.

***

## File Schemas

**`manifest.json`**

```json
{
  "source_pdf": "abs/path/to.pdf",
  "n_pages": 350,
  "created_utc": "2026-02-21T05:00:00Z",
  "approx_chars_per_chunk": 5000,
  "overlap_chars": 600,
  "n_chunks": 142,
  "scanned_pdf_hint": false,
  "has_outline": true,
  "chunks_path": "abs/path/to/chunks.jsonl",
  "notes": "If scanned_pdf_hint is true, consider OCR then re-run."
}
```

**`chunks.jsonl` (one per line)**

```json
{
  "chunk_id": "c00012",
  "pages": "41-43",
  "heading_path": ["Security", "Policy Requirements"],
  "text": "…chunk content with inline [[Page N]] markers…"
}
```
