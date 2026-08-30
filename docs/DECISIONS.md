# DECISIONS

What was chosen, what was rejected, and why — so that none of it is relitigated
from scratch in three months, and so a later "obvious improvement" can be
recognised as a decision already made.

Each entry: **the decision**, the alternative, and the evidence.

---

## 1. The name is `MCP_Documents`, not `MCP_PDF` or `MCP_Files`

**Rejected `MCP_Files`** — it collides with `MCP_File_System`, which exists. Not
just conceptually: it would put `FS_MCP_URL` beside `FILES_MCP_URL`, two hosts
one word apart, and two mounts one letter apart in a flat tool list. The `files`
name is also already taken by the harness container that serves the shared
exchange directory. Three collisions on one word.

**Rejected `MCP_PDF`** — it contradicts the decision that makes the tool count
small: the format-agnostic front door (ARCHITECTURE §3). And **names shape what
gets added**. A repo called `MCP_PDF` grows PDF tools, which is this project's
number-one risk. `MCP_Documents` makes "add an epub reader" feel native and "add
a fifteenth PDF button" feel wrong. That pressure is the point.

Prefix `DOCS`, mounts `docs-read` / `docs-edit`. Plural, so they stay
distinguishable from Office's four `office-docx-*` mounts.

---

## 2. One tool per operation. Never a `list[dict]` "ops" parameter

**The tempting design** is one `pdf_pages` tool taking
`ops: [{"op": "merge", …}, {"op": "rotate", …}]`. It collapses six tools into
one and looks like good API design.

**The evidence against it is in this fleet.** `MCP_File_System` is built that
way — six tools hiding ~35 operations behind an `op` word — and it has the
**worst defect rate of the six repos, 0.50 defects per tool**, with a defect in
three consecutive sweep rounds. Two specific failures:

- **The vocabulary is invisible to every schema check.** The keys sit one level
  below the schema, so neither pydantic nor any argument validation can see them.
  Five of one round's eight defects lived inside a single ops dict.
- **It hides from coverage.** Six rounds of "call every tool once" reached six of
  the ~35 operations. The tool answered every time. Nothing was being tested.

**What is allowed instead:** a single typed `str` parameter from a documented
set — `convert(to="docx")`, `optimize(action="compress")`. One value, one level,
visible in the response, checkable.

**And one documented string grammar**, for `assemble` — with the requirement
that the parsed result is **echoed back in the response**. A grammar whose
interpretation cannot be inspected is the banned ops-dict wearing a different hat.

---

## 3. Thirteen tools, not twenty-five

A commercial PDF site exposes roughly 25 tools. That number is a property of
**user interfaces**: a UI needs one button per verb, because a button cannot
take an argument.

An agent does not. Merge, split, extract pages, remove pages, organise and
rotate are **one operation** — *produce a document from a page selection* —
and `assemble` does all six, plus interleaving, which no button does. `convert`
absorbs ten more.

The requirement driving this was explicit: *small number of servers and tools,
so it is easier for AI agents to use*. Every tool in the list is context the
model spends before it does anything, and the target is a 9B model with ~10k of
context.

**The 14th tool is the failure mode**, not a missing capability. Almost every
"we need a tool for X" is a parameter on a tool that exists.

---

## 4. Extraction is the product; manipulation is table stakes

The manipulation half — merge, split, convert, compress — is a solved problem
with good libraries, and any competitor has it.

The half that is worth building is **extraction from documents too large to
read**, because that is where the agent's context limit bites and where a web UI
cannot help at all. A site processes one file, one operation, one upload at a
time. An agent chaining these tools does *"every invoice in this folder → detect
the scanned ones → OCR only those → pull the totals table → merge into one
workbook"*, which is the research → analytics → reporting path the fleet exists
to serve.

So `probe` / `find` / `extract` get the design attention, and the extraction
tools write output where the sibling data server can load it.

---

## 5. Provenance in every response, from day one

Not a v2 feature. Retrofitting `basis` means auditing every call site, and by
then callers have learned to trust unqualified answers.

The justification is the fleet's own defect history: ~145 defects over 21 sweep
rounds, and **almost none of them was a failing check** — every one returned
`success: true`. The recurring shape is *the number is right and the sentence
taken from it is wrong*. A server that reconstructs paragraphs and tables from
glyph coordinates is a machine for producing exactly that, unless it is designed
against it. See ARCHITECTURE §4.

---

## 6. Permissive licences by default, copyleft only by decision

The fastest PDF library available is **PyMuPDF, and it is AGPL-3.0**. The best
compressor is **Ghostscript, also AGPL-3.0**. Both are likely fine for a
self-hosted server, and both are a decision that should have a name on it rather
than arriving through a transitive dependency.

The default stack — `pypdfium2` + `pdfplumber` + `pikepdf` — is chosen to avoid
every copyleft entry, at some cost in speed. Changing that is allowed; changing
it silently is not. `TECH_STACK.md` §2 is the register.

---

## 7. PDF → docx will not match the commercial converters, and will say so

Commercial sites use commercial reconstruction engines. There is **no CPU-only
open-source path to that quality**, and pretending otherwise sets the one
expectation users will actually test.

So: ship it, label the output `basis: "reconstructed"` with a note naming what
was uncertain (column count, table inference), and let `probe` say when a
document is a poor candidate before the conversion is attempted.

**Specifically rejected:** routing PDF → docx through LibreOffice's Draw import
filter. It produces a document in which every line of text is a separate
floating text box. It opens, it contains the right words, it is unusable, and it
reports success — the precise defect shape this fleet's sweeps exist to catch.

---

## 8. `redact` verifies, or it fails

Drawing a black rectangle over text leaves the text fully extractable
underneath. That is how redaction failures reach the news, and it is the only
tool in this repo whose failure hurts a person rather than an agent.

The contract: remove glyphs and content streams, **then re-extract the region
and confirm nothing matches**, and report that verification in the response.
`success: true` with `verified: false` must be impossible.

It is scheduled **last** in the build order, deliberately. A half-built `redact`
is worse than no `redact`, because its existence is a claim.

Related scope decision: `protect(action="decrypt")` requires the password. This
repo does not attempt to recover a password it was not given, whatever the
"unlock" framing on comparable sites. Removing owner-permission flags from a
document the caller can already open is supported, and is what "unlock" usually
means in practice.

---

## 9. Just-in-time, with one deliberate exception

No index build step, no background jobs, no vector store, no persisted state —
the same character as the rest of the fleet, and an explicit requirement.

**The exception is an in-process LRU** keyed on `(path, mtime, size)`. Without
it, the intended `probe` → `find` → `extract` sequence parses a 300-page file
three times: 0.5s versus 30s. It persists nothing and adds no tool.

The boundary that must hold: **the moment it acquires a build step, this is a
different product.** A cache is invisible; an index is a lifecycle.

---

## 10. Fixtures before tools

The fixture corpus in `CLAUDE.md` §12 gets built in Phase 2, **before** the
tools that read it.

A synthetic one-page PDF exercises nothing that matters. Every hard case in this
repo — two-column reading order, unruled tables, running heads, hyphenation
across a page break, hybrid scanned/digital documents, the budget refusals —
is invisible without a fixture designed to expose it.

The fleet's highest-yield technique is *build the input that separates a good
answer from a lucky one*: a 95/5 imbalanced target once made a classifier report
95% accuracy and an f1 of 0.926, both arithmetically correct, having found zero
of the ten positives. **Fixtures where the headline number lies** find what real
documents hide.

The budgets in particular are untestable without a genuinely large document, so
the 500-page fixture is not optional.
