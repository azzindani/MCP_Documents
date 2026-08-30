# ARCHITECTURE

## 1. The problem this shape solves

A 500-page PDF holds roughly **250,000 tokens**. The target agent has about
**10,000** of context. Every design decision below follows from that one ratio.

The naive document server returns text. It works on a 3-page invoice and is
useless on everything people actually have. So this server does not return
documents — it makes them **addressable**.

---

## 2. The three-step path

```
        probe                 find                    extract
   ┌──────────────┐     ┌───────────────┐      ┌──────────────────┐
   │ what is this │ --> │ WHERE is it   │ -->  │ this region only │
   │ how big      │     │ locations     │      │ cleaned          │
   │ what's scanned│     │ counts        │      │ with its basis   │
   │ where's the  │     │ snippets      │      │                  │
   │ structure    │     │ never content │      │                  │
   └──────────────┘     └───────────────┘      └──────────────────┘
      ~300 tokens          ~600 tokens          caller-bounded
```

`find` returning **locations rather than content** is the load-bearing decision.
It is what lets an agent answer "what is the total on the Acme invoice" against a
500-page bundle inside a 10k context: find the pattern (47 hits, pages listed),
extract one page.

The docstrings must teach this order, because live MCP schemas carry no parameter
descriptions — 80 characters per tool is the entire contract the model reads.

---

## 3. One intermediate representation, many readers

**No tool asks what format it was given.**

```
                     ┌─ readers/pdf.py    (pypdfium2 + pdfplumber)
                     ├─ readers/html.py   (lxml)
   source ──────────>├─ readers/ooxml.py  (python-docx / openpyxl / python-pptx)
   path or URL       ├─ readers/email.py  (stdlib email)
                     ├─ readers/epub.py   (zipfile + lxml)
                     └─ readers/text.py
                              │
                              v
                      engine/ir.py
              Document → Page → Block → Span
                              │
        ┌─────────────────────┼─────────────────────┐
        v                     v                     v
   clean.py             order.py               tables.py
   running heads        columns                ruled / whitespace
   de-hyphenation       reading order
        └─────────────────────┼─────────────────────┘
                              v
                     every docs-read tool
```

```python
Document(source, format, pages[], meta)
Page(number, width, height, rotation, blocks[], basis, confidence)
Block(kind, bbox, spans[], order)   # para|heading|table|figure|caption|list|head_foot
Span(text, bbox, font, size, flags)
```

**This is the decision that keeps the tool count at 13.** Adding `.epub` support
costs one reader, not five tools. A reader that genuinely has no coordinates —
an email body, plain text — supplies `bbox=None`, and the layout-dependent tools
say so rather than inventing geometry.

`source` accepts an `http(s)` URL when `MCP_FETCH_URLS=1`, resolved by
`shared/exchange.py` before any reader sees it. HTML-from-the-web and
HTML-from-disk are the same code path.

---

## 4. Provenance: `basis` is a field, not a footnote

A PDF is glyphs at coordinates. There is no paragraph, no table, no reading
order and no heading in the file — every one of those is **reconstructed**.

This fleet's entire defect history says the same thing: the number is right and
the sentence taken from it is wrong. A document server is the highest-risk
surface for that yet, so reconstruction announces itself:

| `basis` | Means | Trust |
|---|---|---|
| `text_layer` | glyphs the file actually contains | high |
| `tagged` | the PDF's own structure tree (PDF/UA) | high |
| `ruled` | table found from ruling lines | high |
| `ocr` | recognised from pixels, carries `confidence` | medium |
| `whitespace` | table inferred from column gaps | **low, and says so** |
| `font_size` | heading guessed from typography, not a bookmark | low |
| `reconstructed` | pdf → docx/xlsx/pptx output | varies |

**Per page where it varies.** A hybrid PDF is born-digital on page 1 and a scan
on page 40; one figure for the document would be a lie about both. `probe`
returns `page_kinds` and a `scanned_pages` selection string that can be passed
straight to `ocr()`.

The test of this design: a tool that says *"14 tables"* and a tool that says
*"9 from ruling lines, 5 inferred from column gaps, and page 22 is an un-OCR'd
scan"* are different products. This is the second.

---

## 5. `clean.py` — the difference between 300 usable pages and 300 pages of noise

Applied in order, each step individually disableable, **each step reporting what
it changed**:

1. **Running heads and feet.** Lines appearing at the same y-position on more
   than 60% of pages. Cheap to detect across the document, and the difference
   between usable text and *"CONFIDENTIAL — Acme Corp — Page 47"* every 40 words.
2. **De-hyphenation** across line and page breaks: `manu-\nfacturing` →
   `manufacturing`, only where the joined form is the more plausible token.
3. **Unicode normalisation**: ligatures `ﬁ ﬂ`, soft hyphens, non-breaking
   spaces, common mojibake.
4. **Whitespace collapse** that preserves paragraph boundaries.

`extract` returns a `cleaned` object counting every edit. **A cleaner that
silently rewrites text is indistinguishable from a corrupt extraction** — the
count is what makes the difference visible.

---

## 6. `order.py` — reading order

Multi-column detection, then XY-cut ordering within each column.

Getting this wrong is **the single commonest way PDF text is silently garbage**:
a two-column page read straight across interleaves every sentence with an
unrelated one, and the output looks like plausible English while meaning nothing.

Column count is *detected*, *reported* in every response that returns text, and
never assumed to be 1.

---

## 7. `budget.py` — three limits that are part of the contract

| Budget | Computed from | Refusal names |
|---|---|---|
| **tokens** | estimated document tokens vs the response ceiling | a page range that fits |
| **pixels** | `width × height × dpi² × 3` bytes vs the container limit | the DPI that would fit |
| **time** | pages × seconds-per-page for OCR vs the call timeout | the scanned page range from `probe` |

Every limit produces `success: false` with a `hint` naming a specific value.
**Never a silent sample and never an unannounced truncation.** A sibling repo
lost twelve tools at once to an unbudgeted memory cliff, and the caller saw only
a closed socket.

---

## 8. `cache.py` — the one place statelessness bends

Just-in-time, like the rest of the fleet: no index build, no background jobs, no
vector store, no persisted state.

**The exception:** an in-process LRU keyed on `(path, mtime, size)`. Without it,
`probe` → `find` → `extract` on a 300-page file parses it three times — 0.5s
versus 30s. It persists nothing, is invisible to the caller, adds no tool, and
invalidates itself the moment the file changes.

It must not grow into an index. The moment it has a build step, this is a
different product with different failure modes.

---

## 9. Deployment shape

Two sub-servers on one port via `unified_server.py`, each at a path prefix —
identical to Data_Analyst, Machine_Learning and Microsoft_Office.

```
unified_server.py
 ├── /read/mcp   docs-read   7 tools
 └── /edit/mcp   docs-edit   6 tools
```

Mount names are **`docs-read`** and **`docs-edit`**, plural. Not `doc-`: Office
already exposes `office-docx-basic`, `office-docx-tables`, `office-docx-layout`
and `office-docx-new`, and `doc-` against `docx-` is one character apart in the
flat list of ~240 tools a model chooses from.

Three deployment facts inherited from the fleet's migrations, all of which cost
real time to find and none of which should be rediscovered here:

- **Drive uvicorn directly** on `streamable_http_app()` and pass
  `timeout_keep_alive` (`MCP_KEEPALIVE_SECONDS`, 300). The SDK builds
  `uvicorn.Config` without it; uvicorn defaults to 5s while a reverse proxy pools
  connections for 2 minutes, so the caller gets a **200 with zero bytes after the
  tool has already run**.
- **Disable DNS-rebinding Host validation** per sub-server, or every tool call
  behind the proxy returns `421 Invalid Host header` while `/health` passes.
- **Each sub-server needs its own OAuth state directory**, `chown 999:999`
  before first run, even under one process — each tier is a separate connector.
