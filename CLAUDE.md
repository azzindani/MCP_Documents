# CLAUDE.md — Documents MCP Server

> AI coding agent instructions. Follow every rule in this file exactly.
> When this file conflicts with the general STANDARDS.md, this file takes precedence.
> Standards reference: https://github.com/azzindani/Standards/blob/main/local_mcp/STANDARDS.md

---

## 1. Project Overview

**documents-mcp-server** is a self-hosted MCP server for reading, extracting from
and manipulating documents — PDF first, but **not PDF only**. It is the seventh
repo in the `MCP_*` fleet and the one that closes the *research* leg of the
research → analytics → reporting path the fleet exists to serve.

Two jobs, in this order of importance:

1. **Extraction from documents too large to read.** A 500-page PDF holds roughly
   250,000 tokens; the target agent has ~10,000 of context. The server's job is
   to make a document *addressable* rather than to hand it over.
2. **Manipulation.** The operations a person expects from a PDF site — merge,
   split, rotate, convert, compress, OCR, protect, redact — done locally.

**Founding constraints (non-negotiable, inherited from the fleet):**
- All execution on local CPU — no GPU, no cloud APIs, no model weights
- Zero document content leaves the machine. This is the whole point: the
  commercial PDF sites are upload-first, and the documents people run through
  them are contracts, invoices, payslips, medical and legal records
- No API keys, no OAuth to third parties, no subscriptions
- Works fully offline after install (network is used only when the *caller*
  passes an http(s) URL as an input path, and that is opt-in — see §11)

**Deployment scope:** local-first stdio by default; HTTP mode behind a reverse
proxy for use as a remote endpoint by AI platforms and harnesses, bearer-token
authenticated. Same shape as the other six repos.

**Target hardware:** 8 GB VRAM, 9B local model, ~10,000–12,000 token context.

**Tier:** two sub-servers (`docs-read`, `docs-edit`). Deployed as one process on
one port via `unified_server.py` with each tier at a path prefix, exactly like
Data_Analyst / Machine_Learning / Microsoft_Office.

---

## 2. Repository Structure

```
MCP_Documents/
├── servers/
│   ├── docs_read/
│   │   ├── server.py            ← MCP entry point, thin wrappers only
│   │   ├── _read_probe.py       ← probe(), outline()
│   │   ├── _read_find.py        ← find()
│   │   ├── _read_extract.py     ← extract(), extract_tables()
│   │   └── _read_page.py        ← read_page(), to_markdown()
│   └── docs_edit/
│       ├── server.py
│       ├── _edit_assemble.py    ← assemble() + the selection grammar
│       ├── _edit_convert.py     ← convert()
│       ├── _edit_optimize.py    ← optimize(), ocr()
│       └── _edit_secure.py      ← protect(), redact()
├── core/
│   ├── __init__.py              ← thin router, re-exports every tool function
│   ├── ir.py                    ← the Document / Page / Block / Span model
│   ├── readers/                 ← one module per input format, all -> ir
│   │   ├── pdf.py  html.py  ooxml.py  email.py  epub.py  text.py
│   ├── clean.py                 ← de-hyphenation, running heads, ligatures
│   ├── order.py                 ← reading order / column detection
│   ├── tables.py                ← ruled + unruled table reconstruction
│   ├── budget.py                ← token, page, pixel and time budgets
│   ├── cache.py                 ← in-process LRU keyed on (path, mtime, size)
│   └── formatter.py             ← structured JSON output builder
├── shared/                      ← copied from the fleet; see §13 rule 13
│   ├── oauth_bridge.py  exchange.py  json_safe.py   ← never fork these
│   ├── token_estimate.py  tool_annotations.py       ← never fork these
│   ├── deploy_auth.py                               ← copied from a multi-tier sibling
│   ├── platform_utils.py  progress.py               ← per-repo by design
├── unified_server.py            ← mounts both tiers on one port
├── tests/
├── install/
├── docs/                        ← ARCHITECTURE, SCHEMA, TECH_STACK, DECISIONS
├── pyproject.toml  uv.lock  .python-version  .gitattributes
├── CLAUDE.md  README.md
```

**File size hard limits (STANDARDS.md §15):** `server.py` 80–120 lines (300 hard);
every other module 100–300 lines (1,000 hard). `core/__init__.py` is a router
only, 30–50 lines.

---

## 3. Tool Inventory

**Total: 13 tools across 2 sub-servers.** This is a hard ceiling.

### `docs-read` — 7 tools

| Tool | Module | Description |
|---|---|---|
| `probe` | `_read_probe.py` | What is this document, and what can be trusted from it |
| `outline` | `_read_probe.py` | Headings / bookmarks / structure with page anchors |
| `find` | `_read_find.py` | Locate a literal or regex across the whole document |
| `extract` | `_read_extract.py` | Clean content for a bounded region |
| `extract_tables` | `_read_extract.py` | Tables from a page range, as rows |
| `read_page` | `_read_page.py` | Everything on one page |
| `to_markdown` | `_read_page.py` | Whole-document conversion, budget-refused when too big |

### `docs-edit` — 6 tools

| Tool | Module | Description |
|---|---|---|
| `assemble` | `_edit_assemble.py` | Build a document from a page selection |
| `convert` | `_edit_convert.py` | Convert between document formats |
| `optimize` | `_edit_optimize.py` | Compress, repair, linearise, downsample |
| `ocr` | `_edit_optimize.py` | Add a searchable text layer to scanned pages |
| `protect` | `_edit_secure.py` | Encrypt, decrypt, set permissions |
| `redact` | `_edit_secure.py` | Permanently remove content, then verify it is gone |

**No 14th tool may be added without removing one first.** The tool count is the
project's primary risk, not its capability — see §13 rule 1 and `docs/DECISIONS.md`.

`assemble` is deliberately one tool covering what a PDF website presents as six
buttons (merge, split, extract pages, remove pages, organise, rotate). They are
one operation — *produce a document from a page selection* — and the six-button
split is a property of user interfaces, not of the operation.

---

## 4. Architecture Principles

### 4.1 Engine / server split (STANDARDS.md §14)

`servers/*/server.py` contains zero domain logic; every `@mcp.tool()` body is one
line. `core/` contains zero MCP imports.

### 4.2 One intermediate representation, many readers

**A tool must never ask what format it was given.** Every reader in
`core/readers/` normalises its input into the same `Document → Page → Block →
Span` model in `core/ir.py`, and every tool operates on that.

This is the decision that keeps the tool count at 13. Supporting a new format
costs *one reader*, not five tools. PDF, HTML, docx/xlsx/pptx, .eml/.msg, .epub,
plain text, markdown and CSV all enter the same way.

**The claim has to be enforced, not just stated.** It was untrue for two of the
tools until the second reader existed: `_read_probe.py` imported
`core.readers.pdf` directly and `_read_page.py` / `_read_extract.py` imported
pdfplumber, so `probe`, `outline`, `read_page` and `extract_tables` — the entry
points every workflow starts with — would have run PDF machinery over an HTML
file. **Nothing above `core/readers/` may import a reader module by name, or a
format's library.** Ask the router; where the question does not apply to a
format, it answers empty.

**Pages are real or synthetic, and the response always says which.** PDF, slides,
worksheets and epub spine items have boundaries the file declares
(`pagination: "native"`). HTML, Word, email, markdown and text are a flow with
no pages, so pages are made at `budget.flow_chars_per_page()` and reported as
`pagination: "synthetic"` alongside `chars_per_page`. A caller told "page 7 of
12" for a web page, who then finds no such division in the source, has been
misled about the one thing this server exists to be careful with.

A native page too large to read is divided anyway, and the document then reports
`synthetic` — see the note in `core/readers/office_xlsx.py`. A page that cannot
be extracted, whose refusal names a range that cannot be narrowed, is worse than
no tool.

### 4.3 No tool ever returns a document

The reason the server exists is that documents do not fit in the caller's
context. Every content-returning tool is bounded, and the intended path is:

```
probe   ->  what is this, how big, what is in it, what is trustworthy   (tiny)
find    ->  WHERE things are: locations, counts, snippets               (bounded)
extract ->  one region the caller chose, cleaned                        (chosen)
```

`find` returns **locations, not content**. A tool that answers a question by
returning the document has failed even when the answer is in there.

### 4.4 Provenance is a field, not a footnote

A PDF is glyphs at coordinates. Paragraphs, tables, reading order and headings
are *reconstructed*, and reconstruction is exactly where this fleet's defects
live: the number is right and the sentence taken from it is wrong.

So every extraction carries **how it was obtained**, in the response:

- `basis: "text_layer" | "ocr" | "ruled" | "whitespace" | "tagged"`
- `confidence` where the method has one (OCR score, table structure score)
- per page where it varies — a hybrid PDF is born-digital on page 1 and a scan
  on page 40, and one number for the document would be a lie about both

A tool that says *"14 tables"* and a tool that says *"9 from ruling lines, 5
inferred from column gaps, and page 22 is an un-OCR'd scan"* are different
products. Build the second.

### 4.5 Just-in-time, with one exception

No index build step, no background jobs, no vector store, no persisted state —
the same character as the rest of the fleet.

**The one exception is `core/cache.py`:** an in-process LRU keyed on
`(path, mtime, size)`. `probe` → `find` → `extract` on a 300-page file otherwise
parses it three times, which is the difference between 0.5s and 30s. It persists
nothing, is invisible to the caller, and adds no tool. Do not grow it into an
index.

### 4.6 Self-hosted execution (STANDARDS.md §4)

Every tool must answer yes to: **can this run with the machine offline?** The
only network use is `MCP_FETCH_URLS=1` resolving a caller-supplied http(s) input
path, which is opt-in and off by default.

### 4.7 Budgets are part of the contract

Three limits are real and must be enforced in `core/budget.py`, each with a
refusal that names the limit and the way to stay inside it:

| Budget | Why | Behaviour at the limit |
|---|---|---|
| **tokens** | a 500-page document is ~250k tokens against ~10k of context | refuse, name the page range syntax |
| **pixels** | a 300 DPI A4 page is ~25 MB as RGB, against a 1 GiB container | refuse, name the DPI that would fit |
| **time** | OCR is 1–3 s/page/core; 200 pages exceeds any call timeout | refuse, name the page range |

A refusal naming `n` and the limit is always better than a silent sample. The
fleet has already lost twelve tools at once to a memory cliff that was not
budgeted (DBSCAN, ~4.1 GB against 1 GiB).

---

## 5. Tool Schema Design (STANDARDS.md §11)

### Docstring rule: ≤ 80 characters, machine-readable

Live MCP schemas carry **no enums and no parameter descriptions** — every string
property arrives as a bare `{"type": "string"}`. The 80-character docstring is
therefore the *entire* contract the model reads. CI enforces the limit with
`verify_tool_docstrings.py`.

Because the docstrings are the only contract, they must teach the **order**:
`probe` before `find`, `find` before `extract`.

### Parameter types — only these are permitted

`str`, `int`, `float`, `bool`, `list[str]`, `list[float]`, `dict[str, str]`.

Never `Optional[T]`, `Union`, `Any`, untyped `dict`, custom Pydantic models, and
**never `list[dict]`** — see §13 rule 2.

### Tool annotations (STANDARDS.md §12)

`docs-read` tools: `readOnlyHint=True, destructiveHint=False, idempotentHint=True,
openWorldHint=False`.

`docs-edit` tools write files: `readOnlyHint=False`. `redact` and `protect` are
`destructiveHint=True`. Set these from the **official SDK's** `ToolAnnotations`
type, never a bare dict.

---

## 6. Sub-Module Design

### 6.1 `core/ir.py`

```
Document(source, format, pages[], meta)
Page(number, width, height, rotation, blocks[], basis, confidence)
Block(kind, bbox, spans[], order)        kind: para|heading|table|figure|caption|list|head_foot
Span(text, bbox, font, size, flags)
```

Everything downstream reads this. A reader that cannot supply `bbox` (plain
text, email bodies) supplies `None` and the layout-dependent tools say so rather
than inventing coordinates.

### 6.2 `core/clean.py`

The difference between 300 usable pages and 300 pages of noise. In order:

1. **Running heads and feet** — lines at the same y-position on >60% of pages.
   Detected across the document, removed, and **reported as removed**.
2. **De-hyphenation** across line breaks (`manu-\nfacturing` → `manufacturing`),
   only where the joined form is more plausible than the split one.
3. **Unicode normalisation** — ligatures (`ﬁ`/`ﬂ`), soft hyphens, non-breaking
   spaces, common mojibake.
4. **Whitespace** — collapse without destroying paragraph boundaries.

Every step is individually disableable and every step reports what it changed. A
cleaner that silently edits text is indistinguishable from a corrupt extraction.

### 6.3 `core/order.py`

Reading order and column detection. **Getting a two-column layout wrong makes
every sentence interleave with the wrong one**, which is the single commonest way
PDF text is silently garbage. Column count is detected, reported in the response,
and never assumed to be 1.

### 6.4 `core/tables.py`

Two paths, and the response always says which ran:

- **ruled** — ruling lines present; high confidence, ~90%+ on real documents
- **whitespace** — column-gap clustering; genuinely uncertain, and must say so

Never report an unruled reconstruction with the same confidence as a ruled one.

### 6.5 `_edit_assemble.py` — the selection grammar

One tool, one documented grammar, one typed `str` parameter:

```
assemble(sources=["a.pdf", "b.pdf"], select="a:1-5, b:all, a:9r90", out="…")
```

- `merge`   several sources
- `split`   call it twice with complementary selections
- `remove`  select the complement
- `reorder` list the pages in the order wanted
- `rotate`  the `r90` / `r180` / `r270` suffix

The parsed selection **must be echoed back in the response** so a caller can see
what the grammar understood before trusting the output. A grammar that cannot be
checked is the op-dispatcher problem wearing a different hat.

---

## 7. Return Value Contract (STANDARDS.md §16)

Every tool returns a `dict`. No plain strings, lists, `None` or `bool`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `success` | `bool` | Always | First key checked by the caller |
| `op` | `str` | Always | Operation name; **must differ per tool** |
| `result` | varies | On success | The value |
| `basis` | `str` | On any extraction | How it was obtained — §4.4 |
| `error` | `str` | On failure | Human-readable |
| `hint` | `str` | On failure | Actionable, names a specific tool or fix |
| `progress` | `list` | Always | Execution log |
| `token_estimate` | `int` | Always | `len(str(response)) // 4` |

**Every number must be a real JSON number.** No `NaN`, no `Infinity`, no numbers
delivered as quoted strings, no object reprs, no `numpy.float64(...)` wrappers.
Those are not valid JSON and a non-Python client cannot read them. This is not
theoretical: a sibling repo shipped `"mean": Infinity`, which round-trips
perfectly through Python and breaks every JS or Go client.

---

## 8. Error Handling Contract (STANDARDS.md §17)

All exceptions caught in the engine; none propagate to `server.py`. Every error
dict carries `success: False`, `op`, `error`, `hint`, `token_estimate`.

Hints must name a specific tool, parameter or value. Never "Invalid input."

```python
"hint": "This page has no text layer. Run ocr(pages='40-45') first, then retry."
"hint": "Document is 512 pages (~250k tokens). Use extract(pages='1-20')."
"hint": "Rendering 300 pages at 300 DPI needs ~7 GB. Use dpi=96 or a page range."
```

An error computed from what went wrong, beside a hint chosen by the call site,
is the commonest defect shape in this fleet. Route `except Exception` hints
through a helper that switches on the exception type.

---

## 9. Token Budget (STANDARDS.md §20)

Per-response targets: `probe` ≤300, `outline` ≤400, `find` ≤600, `extract` caller
-bounded with a hard ceiling, `extract_tables` ≤800, `read_page` ≤800,
`to_markdown` refuses above the ceiling, all `docs-edit` tools ≤200.

Use `get_max_results()` / `is_constrained_mode()` from `shared/platform_utils.py`.
Never hardcode a limit. `MCP_CONSTRAINED_MODE=1` tightens every budget in §4.7.

---

## 10. Python / Tooling Standards (STANDARDS.md §5)

- Python **3.14** — `requires-python = "==3.14.*"`, `.python-version` = `3.14`,
  Dockerfile `ARG PYTHON_VERSION=3.14-slim`, both workflows
- Package manager: `uv` only
- Lint + format: `ruff` only (`line-length = 120`, `target-version = "py314"`)
- Type checking: `pyright`, `pythonVersion = "3.14"`. **If a `pyrightconfig.json`
  exists it overrides `[tool.pyright]` in pyproject.toml** — this cost a CI round
  in a sibling repo. Do not create one.
- Testing: `pytest`
- MCP SDK: the **official** `mcp` package, `mcp>=1.0,<2.0`, high-level API
  `mcp.server.fastmcp.FastMCP`. **Never the third-party `fastmcp` 2.x package.**

Library choices and pins: `docs/TECH_STACK.md`. Do not add a dependency that
carries model weights, needs a GPU, or requires a JVM.

---

## 11. Transport, Auth and Install (STANDARDS.md §30, §31)

`--transport stdio` (default) and `--transport http --port <port>`.

**HTTP mode must drive uvicorn directly on `streamable_http_app()`**, not
`mcp.run("streamable-http")`, and must pass `timeout_keep_alive`
(`MCP_KEEPALIVE_SECONDS`, default 300). The SDK builds `uvicorn.Config` without
that setting; uvicorn's default is 5s while a reverse proxy pools upstream
connections for 2 minutes, so any connection idle between the two is dead at this
end and live in the pool. The caller then gets a 200 with zero bytes **after the
tool has already run**, which a retry turns into a double-apply. All six sibling
repos carry this fix; do not reintroduce it here.

Also from the fleet's migration: **DNS-rebinding Host validation is on by
default** and produces `421 Invalid Host header` on every tool call behind a
proxy while `/health` passes. Set
`TransportSecuritySettings(enable_dns_rebinding_protection=False)` per sub-server.

Bearer auth via `shared/deploy_auth.py`, `build_token_verifier("DOCS")`:
`DOCS_TOKENS_FILE` > `DOCS_TOKENS` > `DOCS_API_KEY` > open. Open mode is for
localhost only, never for a reachable deployment. Keys live only in a gitignored,
chmod-600 `.env`. **Credentials are rotated by the operator, never by an agent.**

File exchange via `shared/exchange.py`, byte-identical with the four
file-producing repos: `MCP_OUTPUT_DIR`, `MCP_PUBLIC_BASE_URL`, `MCP_FETCH_URLS`.
`MCP_FETCH_URLS=1` is what makes *"everything is fetchable"* work — every path
argument accepts an http(s) URL with no per-tool change.

Install path `~/.mcp_servers/documents-mcp-server`. Env var `MCP_CONSTRAINED_MODE`.

**Mount names are `docs-read` and `docs-edit`, plural.** Not `doc-`: Office
already exposes `office-docx-basic`, `office-docx-tables`, `office-docx-layout`
and `office-docx-new`, and `doc-` against `docx-` is one character apart in a
flat list of 240 tools that a model chooses from.

---

## 12. Testing Standards (STANDARDS.md §27)

Tests import `engine` router directly — never spin up an MCP process.

Required per tool: happy path, malformed input, budget refusal, constrained
mode, `token_estimate` present, and **for every extraction tool, a `basis` that
matches how the fixture was actually built**.

Fixtures must include, because these are what break extraction and no synthetic
one-page PDF exercises any of them:

- a **born-digital** PDF and a **scanned** one and a **hybrid** of both
- a **two-column** layout — the reading-order case
- a **ruled** table and an **unruled** one
- a document with **running heads and feet** on every page
- a **hyphenated** line break across a page boundary
- an **encrypted** PDF and a **damaged** one
- a **500-page** document, for the budgets — the budgets are untestable without it
- the same content as **HTML, .docx and .eml**, to prove the readers agree

Coverage: `shared/` 100%, `core/` 90%, servers 90%.
CI on `ubuntu-22.04`, `macos-latest`, `windows-latest`, `fail-fast: false`.

`remote_smoke_test.sh` exercises a running server over HTTP, run in CI against a
container via the shared `e2e-smoke` action. **Read values out of the MCP
envelope with `\\?"key\\?"[[:space:]]*:`** — a tool's document arrives as the JSON
*string* `result.content[0].text`, so keys and values are escaped on the wire.
Patterns written for unescaped JSON match nothing while every call still
succeeds; four of six sibling repos silently stopped asserting anything that way.
End any such extractor with `|| true`, or `set -euo pipefail` aborts the script
before its own `|| fail` can report it.

`tests/test_smoke_test_covers_every_tool.py` reads every `@mcp.tool()` name out
of the server modules' AST and fails if one never appears in the smoke script.

---

## 13. What the AI Must Never Do

1. **Never add a 14th tool.** The tool count is this project's primary risk. A
   PDF site has ~25 tools because it is a UI and a UI needs one button per verb;
   an agent needs verbs with parameters. If a capability seems to need a new
   tool, it is almost always a parameter on an existing one.
2. **Never use a `list[dict]` "ops" parameter.** The keys sit one level below the
   schema, so neither pydantic nor any argument check can see them. The sibling
   repo built this way, File_System, has the **worst defect rate in the fleet
   (0.50 per tool)** and a defect in three consecutive sweep rounds; six rounds
   of one-call-per-tool reached six of its ~35 hidden operations. One tool per
   operation, real typed parameters, or one documented string grammar that is
   parsed and **echoed back**.
3. **Never return a whole document** from a tool. See §4.3.
4. **Never report a reconstruction without its basis.** See §4.4.
5. **Never ship a `redact` that draws a rectangle.** Covering text leaves it
   fully extractable underneath. `redact` must remove glyphs and content streams
   and then **re-extract the region to verify nothing remains**, and the response
   must report that verification. This is the one tool here that can hurt a
   person; if it cannot verify, it must fail rather than claim.
6. **Never claim PDF → docx/pptx parity with commercial converters.** They use
   commercial reconstruction engines; there is no CPU-only open-source path to
   that quality. Ship it, and let `probe` say when a document is a poor
   candidate. Never convert a PDF to .docx through LibreOffice's Draw import
   filter: it produces a document where every line is a separate floating text
   box — it opens, it has the right words, it is unusable, and it reports success.
7. **Never assume single-column** reading order. Detect and report it.
8. **Never load a whole document to answer a bounded question.** Readers are
   page-lazy; `find` streams.
9. **Never persist state.** The LRU in `core/cache.py` is memory-only and must
   not grow into an index. No `.mcp_versions/`, no receipt log — except where a
   `docs-edit` tool overwrites a file, which follows the fleet's snapshot rule.
10. **Never print to stdout** — `logging` to stderr only.
11. **Never hardcode a numeric limit** — `shared/platform_utils.py` and
    `core/budget.py`.
12. **Never import MCP outside `servers/*/server.py`.**
13. **Never fork the fleet-common half of `shared/`.** `oauth_bridge.py`,
    `exchange.py`, `json_safe.py`, `token_estimate.py` and `tool_annotations.py`
    are common: a fix belongs in all seven or in none.

    Measured across the six siblings rather than assumed, because the fleet's
    own notes overstate it:
    - `oauth_bridge.py` — five repos byte-identical, File_System differs by **77
      lines of pure line-wrapping** and nothing else, because it lints at
      `line-length = 100` and the rest at 120. Functionally one file.
    - `exchange.py` — four identical; File_System's differs deliberately (its
      `path` is a destination, so URL fetching lives in a `download` op).
    - `deploy_auth.py` — **genuinely six versions**, 60 to 137 lines. Copy from a
      *multi-tier* sibling (Data_Analyst / ML / Office), because this repo has
      tiers and the single-server copies do not thread a per-tier `base_url`.
    - `platform_utils.py` and `progress.py` — **per-repo by design**; the limit
      helpers are domain nouns (`get_max_rows`, `get_max_lag`). Do not unify
      them, and do not import another repo's.

    When porting a fix into the common five, `diff` before assuming a repo is
    behind: a 77-line diff that is entirely reflow is not a missing fix.
14. **Never put a hostname, domain or token in this file or any doc in this
    repo.** These are shipped to third-party model providers on every harness
    session. Use `DOCS_PUBLIC_URL` and friends.

---

## 14. Progress Tracker

### Phase 0 — Design  ✅
- [x] Scope, tool inventory and tiering agreed (13 tools, 2 sub-servers)
- [x] `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/SCHEMA.md`,
      `docs/TECH_STACK.md`, `docs/DECISIONS.md`
- [x] `.python-version`, `.gitattributes`, `pyproject.toml`

### Phase 1 — Scaffolding  ✅
- [x] `shared/` copied from siblings (see §13 rule 13 for which are common)
- [x] `uv sync` clean on 3.14 — every pin has a cp314 or pure-Python wheel
- [x] `unified_server.py` mounting both tiers, `/health` answering
- [ ] Dockerfile, docker-compose, CI (`ci.yml`, `release.yml`), e2e job

### Phase 2 — The IR and readers  ✅
- [x] `core/ir.py`, `core/cache.py`, `core/paths.py`, `core/binaries.py`
- [x] `readers/pdf.py` (pypdfium2 + pdfplumber)
- [x] `html.py`, `text.py` (txt/md/csv/log), `office_docx.py`, `office_pptx.py`,
      `office_xlsx.py`, `eml.py`, `epub.py` — 16 extensions, no new tool
- [x] `core/readers/_flow.py` — synthetic pagination for formats with no pages,
      at 2,800 chars (measured: median of 103 real pages across 25 documents)
- [x] Reader protocol: `open_document` / `close_document` / `load_page` /
      `load_page_words` required; `ruled_pages` / `page_tables` / `bookmarks` /
      `probe_extras` optional, answered empty by the router where a format has
      no such question. Every reader raises `ReaderError`, caught once.
- [x] Fixture corpus per §12, built **before** the tools that read it, plus
      `tests/fixtures/real.py` pointing at real documents (never copied)

### Phase 3 — `docs-read`  ✅
- [x] `probe` · `outline` · `find` · `extract` · `extract_tables` ·
      `read_page` · `to_markdown`
- [x] `core/clean.py`, `core/order.py`, `core/tables.py`, `core/budget.py`

### Phase 4 — `docs-edit`  ✅
- [x] `assemble` — the grammar, with the parse echoed back
- [x] `convert` · `optimize` · `ocr` · `protect`
- [x] `redact` **last**, with its verification step
- [ ] `convert(to='pdf')` is written but untested here — needs LibreOffice
- [ ] `optimize` image downsampling — needs a Ghostscript decision (AGPL)

### Phase 5 — Deployment
- [ ] Dockerfile carrying LibreOffice + Tesseract, and a memory limit decision
- [ ] Bearer auth and OAuth bridge wired end to end (code is in place)
- [ ] `remote_smoke_test.sh` + the coverage guard
- [ ] Live deployment behind the reverse proxy
