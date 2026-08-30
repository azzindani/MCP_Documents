# SCHEMA — every tool, its signature, and what it returns

13 tools, 2 sub-servers. This file is the contract. If an implementation and this
file disagree, one of them is a defect — decide which, then fix that one.

**Why the docstrings look terse.** Live MCP schemas carry no enums and no
parameter descriptions: every string property reaches the model as a bare
`{"type": "string"}`. The ≤80-character docstring is therefore the *entire*
contract the model reads. CI fails the build on any docstring over 80.

**A note on `action` / `to` parameters.** `convert(to=…)`, `optimize(action=…)`
and `protect(action=…)` take one typed `str` from a documented set. That is
allowed. What is **banned** is a `list[dict]` "ops" parameter, where the
vocabulary sits one level below the schema and nothing can validate it — see
`DECISIONS.md` §2 for the sibling repo that proves the cost.

---

## Common to every response

```jsonc
{
  "success": true,               // always, first key the caller checks
  "op": "extract",               // always, unique per tool
  "result": …,                   // on success
  "basis": "text_layer",         // on any extraction — see ARCHITECTURE §4
  "error": "…",                  // on failure
  "hint": "…",                   // on failure, names a specific tool or value
  "progress": [ … ],             // always
  "token_estimate": 412          // always, len(str(response)) // 4
}
```

Every number is a real JSON number. No `NaN`, no `Infinity`, no numbers as
quoted strings, no `numpy.float64(...)` wrappers, no object reprs.

**`source` is any of:** a local path, a path under `MCP_OUTPUT_DIR`, or — when
`MCP_FETCH_URLS=1` — an `http(s)` URL, downloaded to the inbox by
`shared/exchange.py` with no per-tool code. That is how *"everything is
fetchable"* works.

**`pages` is a selection string** everywhere it appears: `"3"`, `"1-20"`,
`"1-5,9,40-"`, `""` meaning all (subject to budget).

---

# `docs-read` — 7 tools

All `readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False`.

---

### 1. `probe`

```python
def probe(source: str) -> dict:
    """Identify a document: format, pages, scanned or digital, what it holds."""
```

The map. Always the first call, and cheap enough to always be worth it.

```jsonc
"result": {
  "format": "pdf",
  "pages": 512,
  "bytes": 24117248,
  "encrypted": false,
  "tagged": false,                     // PDF/UA structure tree present
  "languages": ["en"],
  "page_kinds": {"born_digital": 470, "scanned": 40, "empty": 2},
  "scanned_pages": "38-77",            // selection string, ready to pass to ocr()
  "columns": {"1": 470, "2": 40},      // detected column counts by page count
  "tables": {"ruled": 9, "whitespace": 5},
  "has_forms": true,
  "running_heads": true,
  "token_estimate_full": 251000,       // why you must not ask for it all
  "extractable": "partial"             // full | partial | none
}
```

`extractable: "partial"` with `scanned_pages` set is the answer to *"why did I
get no text for page 40"* before the caller ever asks it.

---

### 2. `outline`

```python
def outline(source: str) -> dict:
    """List headings and bookmarks with page anchors. Use before extract."""
```

```jsonc
"result": [
  {"level": 1, "title": "Terms", "page": 3, "basis": "bookmark"},
  {"level": 2, "title": "3.1 Payment", "page": 7, "basis": "font_size"}
]
```

`basis` distinguishes a real bookmark from a heading *inferred* from font size —
the second is a guess and must not look like the first. Where a document has
neither, `result` is `[]` and `hint` names `find` as the way to navigate instead.

---

### 3. `find`

```python
def find(source: str, query: str, regex: bool = False, max_hits: int = 50) -> dict:
    """Locate text across a document. Returns page locations, not content."""
```

**Returns where, never what.** This is the tool that makes a 500-page document
usable in a 10k context.

```jsonc
"result": {
  "hits": 47,
  "returned": 47,
  "pages": [3, 17, 44, …],
  "matches": [
    {"page": 3, "line": 14, "snippet": "…total due INV-40128 for 1,204.55 on…",
     "groups": {"inv": "INV-40128", "total": "1204.55"}}
  ]
}
```

With `regex=True` and named groups, `groups` is populated per match — that is the
custom-parsing path: a pattern over 300 pages returns **rows**, which is what a
sibling data server loads. `snippet` is capped; raising `max_hits` past the token
budget is refused with the ceiling named.

---

### 4. `extract`

```python
def extract(source: str, pages: str = "", section: str = "", clean: bool = True) -> dict:
    """Extract clean text for a page range or section. Bounded by design."""
```

`section` takes a title from `outline`. Exactly one of `pages` / `section` is
used; both empty means the whole document and is refused above the token budget
with the page-range syntax named.

```jsonc
"result": {"text": "…", "pages": "1-20", "columns": 2},
"basis": "text_layer",
"cleaned": {"running_heads_removed": 20, "hyphens_joined": 63, "ligatures": 210}
```

`cleaned` reports every edit made to the text. A cleaner that silently rewrites
is indistinguishable from a corrupt extraction — see ARCHITECTURE §5.

---

### 5. `extract_tables`

```python
def extract_tables(source: str, pages: str = "", min_confidence: float = 0.0) -> dict:
    """Extract tables as rows. Reports whether ruling lines or gaps were used."""
```

```jsonc
"result": [
  {"page": 12, "rows": [["Item","Qty"],["Widget","4"]],
   "basis": "ruled", "confidence": 0.96, "shape": [2, 2]},
  {"page": 22, "rows": [ … ],
   "basis": "whitespace", "confidence": 0.51, "shape": [8, 3],
   "note": "column boundaries inferred from gaps; verify before use"}
]
```

A ruled reconstruction and a guessed one must never carry the same confidence.
`min_confidence` lets a caller ask only for the ones worth trusting.

---

### 6. `read_page`

```python
def read_page(source: str, page: int) -> dict:
    """Read one page: text, tables, images, links, and how each was obtained."""
```

Everything on one page, for when the agent needs to actually look. Bounded by
construction — one page cannot exceed the budget except at absurd DPI.

```jsonc
"result": {"page": 12, "text": "…", "columns": 2,
           "tables": [ … ], "images": [{"bbox": [ … ], "dpi": 150}],
           "links": [{"text": "Appendix A", "target": "#page=88"}]},
"basis": "text_layer"
```

---

### 7. `to_markdown`

```python
def to_markdown(source: str, pages: str = "") -> dict:
    """Convert a document to markdown. Refuses when over the token budget."""
```

The convenience path for documents that *do* fit. Above the budget it fails with
`hint` naming both `extract(pages=…)` and the page count that would fit — a
refusal that names `n` and the limit, never a silent truncation.

---

# `docs-edit` — 6 tools

`readOnlyHint=False`. `protect` and `redact` are `destructiveHint=True`.
Every tool writes to `out`, defaulting into `MCP_OUTPUT_DIR`, and **never
overwrites `source`** unless `out == source` is passed explicitly, which takes a
snapshot first.

---

### 8. `assemble`

```python
def assemble(sources: list[str], select: str, out: str) -> dict:
    """Build a document from a page selection: merge, split, reorder, rotate."""
```

One tool covering what a PDF site presents as six buttons. **The grammar:**

```
select := clause ("," clause)*
clause := <source-key> ":" <range> [ rotation ]
range  := "all" | N | N "-" M | N "-"        (1-based, inclusive, "-" = to end)
rotation := "r90" | "r180" | "r270"
```

`<source-key>` is the source's basename without extension, or its index `s0`,
`s1` when basenames collide.

```
"a:1-5, b:all, a:9r90"     merge, with one page rotated
"a:1-10"  then  "a:11-"    split
"a:1-4, a:6-"              remove page 5
"a:3, a:1, a:2"            reorder
```

**The response must echo the parsed selection**, so a caller can check what the
grammar understood before trusting the output:

```jsonc
"result": {"out": "…", "pages_written": 14,
           "parsed": [{"source": "a.pdf", "pages": [1,2,3,4,5], "rotate": 0},
                      {"source": "b.pdf", "pages": [1,2],       "rotate": 0},
                      {"source": "a.pdf", "pages": [9],         "rotate": 90}]}
```

A grammar that cannot be checked is the banned ops-dict wearing a different hat.

---

### 9. `convert`

```python
def convert(source: str, to: str, out: str = "") -> dict:
    """Convert between formats: pdf docx xlsx pptx md html txt images."""
```

Absorbs ten of a PDF site's buttons. The two directions are **not** symmetric and
the response says so:

| Direction | Engine | Quality |
|---|---|---|
| docx/xlsx/pptx/html/md → pdf | LibreOffice headless | high — solved problem |
| pdf → images / txt / md / html | pypdfium2 + the IR | high |
| **pdf → docx/xlsx/pptx** | pdf2docx + the IR | **reconstruction; varies** |

```jsonc
"result": {"out": "…", "to": "docx", "pages": 14},
"basis": "reconstructed",
"note": "2-column layout on 9 pages; verify reading order"
```

Never route pdf → docx through LibreOffice's Draw import filter. It yields a
document where every line is a separate floating text box: it opens, it has the
right words, it is unusable, and it reports success.

---

### 10. `optimize`

```python
def optimize(source: str, action: str = "compress", out: str = "") -> dict:
    """Compress, repair, linearise or downsample a PDF. Reports size change."""
```

`action`: `compress` | `repair` | `linearize` | `downsample`.

```jsonc
"result": {"out": "…", "action": "compress",
           "bytes_before": 24117248, "bytes_after": 3891201, "ratio": 0.161}
```

Report the real byte counts, both sides. A size that rounds to zero reads as
"empty" — a sibling repo shipped `st_size // 1024`, which made every sub-kilobyte
file `0 KB`, including in a delete confirmation.

---

### 11. `ocr`

```python
def ocr(source: str, pages: str = "", language: str = "eng", out: str = "") -> dict:
    """Add a searchable text layer to scanned pages. Page range required."""
```

**The time budget lives here.** Tesseract runs 1–3 s/page/core, so 200 pages
exceeds any call timeout. Empty `pages` means "the pages `probe` said were
scanned", and above the page ceiling it refuses with the range named:

```jsonc
"error": "412 pages would take ~14 minutes, over the 180s limit.",
"hint":  "Run ocr(pages='38-77') — the scanned range probe() reported."
```

```jsonc
"result": {"out": "…", "pages_ocred": 40, "mean_confidence": 0.87,
           "low_confidence_pages": [51, 63]}
```

---

### 12. `protect`

```python
def protect(source: str, action: str, password: str = "", out: str = "") -> dict:
    """Encrypt, decrypt or set permissions on a PDF you have the password for."""
```

`action`: `encrypt` | `decrypt` | `permissions`.

**Scope, deliberately:** decrypt requires the password. This tool does not
attempt to recover a password it was not given, and no amount of "unlock"
framing changes that. Removing owner-permission flags on a document the caller
can already open is supported and is what "unlock" usually means.

---

### 13. `redact`

```python
def redact(source: str, pattern: str, pages: str = "", out: str = "") -> dict:
    """Permanently remove matching content, then verify it cannot be extracted."""
```

**The one tool here that can hurt a person.** Covering text with a black
rectangle leaves it fully extractable underneath, which is how redaction
failures reach the news.

The contract: remove the glyphs and the content streams, **then re-extract the
region and confirm nothing matches**, and report that verification.

```jsonc
"result": {"out": "…", "redacted": 23, "pages": [3, 17, 44],
           "verified": true, "residual_matches": 0}
```

**If verification cannot be performed, the tool fails.** It must never return
`success: true` with `verified: false`. Build the failing case and watch the
guard fire before believing it — a guard never observed to fail is a guard
nobody has tested.

---

## Budget refusals, in one place

| Tool | Limit | Refusal names |
|---|---|---|
| `extract`, `to_markdown`, `find` | token ceiling | the page-range syntax and a range that fits |
| `read_page`, `convert(to="images")` | pixel ceiling | the DPI that would fit |
| `ocr` | time ceiling | the scanned page range from `probe` |
| every `docs-edit` tool | input size | the size seen and the size allowed |

Every refusal is `success: false` with a `hint` naming a specific value. Never
a silent sample, never a truncation that does not say so.
