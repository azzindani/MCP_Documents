# MCP_Documents

**A self-hosted MCP server for reading, extracting from and manipulating
documents — PDF first, but not PDF only.**

The seventh repo in the `MCP_*` fleet, and the one that closes the *research*
leg of the research → analytics → reporting path the fleet exists to serve.

> **Status: design complete, not yet implemented.** See `CLAUDE.md` §14 for the
> build order. The documents in `docs/` are the contract the implementation must
> satisfy.

---

## Why this exists

The commercial PDF sites are upload-first. The documents people actually run
through them are contracts, invoices, payslips, medical and legal records.

**This does the same work and nothing leaves the machine.** No GPU, no cloud
API, no model weights, no subscription — and it works offline.

---

## What it does

**Extraction from documents too large to read.** A 500-page PDF is roughly
250,000 tokens; the agent driving it has about 10,000. So the server does not
return documents, it makes them addressable:

```
probe    what is this — pages, scanned or digital, where the structure is
find     WHERE something is — locations and counts, never the content
extract  one region you chose, cleaned, with a note on how it was obtained
```

That path is what lets an agent answer a question about a 500-page bundle inside
a small context. With a regex and named groups, `find` over 300 pages returns
**rows** rather than prose — which is what the sibling data server loads.

**Manipulation**, the operations a PDF site offers, done locally: assemble
(merge / split / reorder / rotate in one grammar), convert, compress, repair,
OCR, protect, redact.

**Any document, not just PDF.** One reader per format normalising into a single
internal model, so every tool works the same on PDF, HTML, `.docx`, `.xlsx`,
`.pptx`, `.eml`, `.epub`, markdown and plain text. With URL fetching enabled,
every path argument also accepts a link — the same call, whether the HTML came
from disk or the web.

---

## The 13 tools

```
docs-read   probe · outline · find · extract · extract_tables · read_page · to_markdown
docs-edit   assemble · convert · optimize · ocr · protect · redact
```

Thirteen, not the twenty-five a PDF website shows, because that number is a
property of user interfaces — a button cannot take an argument and an agent's
verb can. `assemble` alone covers merge, split, extract pages, remove pages,
organise and rotate.

---

## Documentation

| File | What is in it |
|---|---|
| `CLAUDE.md` | The rules. Read this first if you are an agent working here. |
| `docs/ARCHITECTURE.md` | The three-step path, the intermediate representation, provenance, budgets |
| `docs/SCHEMA.md` | Every tool's signature, response shape and refusals |
| `docs/TECH_STACK.md` | Libraries, licences, external binaries, the container budget |
| `docs/DECISIONS.md` | What was rejected and why — read before proposing a change |

---

## Two things worth knowing before you use it

**Reconstruction announces itself.** A PDF is glyphs at coordinates — paragraphs,
tables, reading order and headings are all *inferred*. Every extraction carries a
`basis` field saying how it was obtained: a table found from ruling lines and one
guessed from column gaps do not get the same confidence, and a page that is an
un-OCR'd scan says so instead of returning nothing.

**PDF → Word/PowerPoint is reconstruction, not conversion.** The commercial sites
use commercial engines and there is no CPU-only open-source path to that quality.
This ships it, labels it, and tells you when a document is a poor candidate.

---

## Install

Requires Python **3.14** and `uv`. Runs as a local stdio server, or over HTTP
behind a reverse proxy with bearer-token auth (`DOCS_API_KEY` /
`DOCS_TOKENS_FILE`). Set `MCP_CONSTRAINED_MODE=1` on small hardware to tighten
every budget.

Install and deployment instructions land with Phase 1.
