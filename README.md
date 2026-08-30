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

Requires Python **3.14** and `uv`. Set `MCP_CONSTRAINED_MODE=1` on small
hardware to tighten every budget.

### Local, as a stdio server

```bash
uv sync
uv run python servers/docs_read/server.py      # 7 read tools
uv run python servers/docs_edit/server.py      # 6 edit tools
```

Two entries in your client's `mcp.json`, one per tier. Everything runs on the
CPU with no network; `convert(to='pdf')` needs LibreOffice and `ocr()` needs
Tesseract, and both say so by name when they are missing rather than failing
inside a subprocess.

### Docker, as a remote endpoint

One container, both tiers on one port, so the PDF stack loads once:

```bash
cp tokens.example.json tokens.json          # or use DOCS_API_KEY
mkdir -p oauth-state shared-files && sudo chown -R 999:999 oauth-state shared-files tokens.json
docker compose up -d --build

curl http://localhost:8850/health            # aggregate
curl http://localhost:8850/read/health       # per tier
```

The image carries LibreOffice and Tesseract. It does **not** carry Ghostscript
— that is a licence decision, not an omission, and `optimize()` reports the
capability it therefore lacks (see `docs/DECISIONS.md` §11). Build with
`--build-arg INSTALL_GHOSTSCRIPT=1` if you accept AGPL for your own deployment.

Mounts are `/read/mcp` and `/edit/mcp`. Auth is bearer-token, by precedence:
`DOCS_TOKENS_FILE` > `DOCS_TOKENS` > `DOCS_API_KEY` > open. **Open mode is for
localhost only** — a reachable deployment with no token set has no auth at all.
Set `DOCS_PUBLIC_URL` to the public origin, or OAuth discovery falls back to the
internal bind address and no remote client can complete it.

To give a caller a link rather than a path inside the container, point
`MCP_SHARED_DIR` at a directory your file server serves and set
`MCP_PUBLIC_BASE_URL` to its URL; every produced file then comes back with a
`public_url`. `MCP_FETCH_URLS=1` additionally lets any `source` argument be an
http(s) link — off by default, and private, loopback and cloud-metadata
addresses are refused even when it is on.

### Checking a deployment

```bash
uv run python -m pytest tests/ -q                     # 205 offline tests
DOMAIN=http://localhost:8850 ./remote_smoke_test.sh   # all 13 tools over HTTP
```

The smoke test is the only thing that exercises LibreOffice and Tesseract, and
it is worth more than its size suggests: it found six defects that 145 passing
tests did not, because it is the only check that hands these tools a document
real software produced. `DOMAIN` has no default on purpose — no hostname
appears anywhere in this repo.
