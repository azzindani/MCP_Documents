# TECH STACK

Python **3.14**, `uv`, `ruff`, `pyright`, `pytest`, official `mcp` SDK — same as
the other six repos, for the same reason: one fleet, one toolchain.

Two rules govern every dependency here:

1. **CPU-only, offline, no model weights, no JVM.** If a library downloads
   weights on first use, it is out — that breaks the offline guarantee and the
   1 GiB container at the same time.
2. **The licence is a decision, not a discovery.** Several of the best PDF
   libraries are AGPL or GPL. That may be perfectly fine for a self-hosted
   server, but it must be chosen deliberately and written down here, not
   inherited by accident from a transitive dependency.

---

## 1. Confirmed permissive — safe to pin now

| Library | Licence | Used for |
|---|---|---|
| `pypdfium2` | Apache-2.0 / BSD-3 (PDFium) | fast text extraction, page rendering, page geometry |
| `pdfplumber` | MIT | word-level coordinates, ruling-line detection, table reconstruction |
| `pdfminer.six` | MIT | pdfplumber's engine; character-level layout |
| `python-docx` | MIT | reading and writing `.docx` |
| `openpyxl` | MIT | reading and writing `.xlsx` |
| `python-pptx` | MIT | reading and writing `.pptx` |
| `lxml` | BSD | HTML/XML parsing |
| `charset-normalizer` | MIT | encoding detection for HTML and email |
| Tesseract (binary) | Apache-2.0 | OCR engine |

These cover: all born-digital PDF reading, all layout and table work, all OOXML,
all HTML, and OCR. That is the majority of the product.

---

## 2. Licence must be confirmed before pinning

**Do not add any of these without checking its current licence at the exact
version being pinned and recording the decision in this table.** Several are
strong-copyleft, and at least one commonly-suggested library is AGPL in a way
that surprises people.

| Library | Believed licence | What it would buy | Alternative if rejected |
|---|---|---|---|
| PyMuPDF / `fitz` | **AGPL-3.0** (commercial available) | the fastest everything | `pypdfium2` + `pdfplumber` — the chosen default |
| Ghostscript | **AGPL-3.0** | best-in-class `compress`, PDF/A | `pikepdf` image recompression; weaker but permissive |
| `pikepdf` / QPDF | MPL-2.0 / Apache-2.0 | all structural surgery: merge, split, encrypt, repair | none as good |
| `ocrmypdf` | MPL-2.0 | orchestrates Tesseract into a real text layer | drive Tesseract directly |
| `pdf2docx` | **verify — believed GPL** | the pdf → docx reconstruction | custom, IR + `python-docx` |
| `ebooklib` | **believed AGPL-3.0** | `.epub` reading | `zipfile` + `lxml`; epub is a zip of XHTML |
| `pyhanko` | MIT | real certificate-based signing | defer signing to v2 |
| `camelot` | MIT **but shells out to Ghostscript** | a second table strategy | `pdfplumber` only |

**The default stack deliberately avoids every copyleft entry above**, which is
why `pypdfium2 + pdfplumber + pikepdf` is the chosen base rather than the faster
PyMuPDF. That is a defensible starting position; changing it is allowed, but it
is a decision with a name on it.

---

## 3. External binaries

| Binary | Needed by | Notes |
|---|---|---|
| **LibreOffice** (`soffice`) | `convert` — anything → PDF | **already installed in the Microsoft_Office image (25.2.3.2)**. Share the base image layer rather than installing a second copy: it is the single largest thing in that image, which is 1.05 GB total. |
| **Tesseract** + language data | `ocr` | ~100 MB with a couple of languages. Install only the languages actually offered. |
| **qpdf** | `optimize(action="repair")` | small, Apache-2.0 |

**Never** convert PDF → docx through LibreOffice's Draw import filter. It
produces a document in which every line of text is a separate floating text box.
It opens, it contains the right words, it is unusable, and it reports success —
the exact defect shape this fleet spends its sweeps hunting.

---

## 4. The container is the constraint

The sibling Office image is **1.05 GB** and runs against a **1 GiB memory limit**.
This repo adds Tesseract and its language data on top of LibreOffice.

Three consequences to design around, not discover:

- **Decide the memory limit up front.** LibreOffice headless converting a large
  document can exceed 1 GiB by itself. A sibling repo once took twelve tools down
  at once when DBSCAN peaked at ~4.1 GB against a 1 GiB container, and the caller
  saw only a closed socket. Either raise the limit for this service or refuse
  oversized conversions with the size named.
- **Render at a pixel budget, not a page count.** A 300 DPI A4 page is ~25 MB as
  RGB; 300 pages is ~7 GB. `engine/budget.py` computes bytes from
  `width × height × dpi² × 3` and refuses with the DPI that would fit.
- **`docker inspect -f '{{.RestartCount}}'`** is how you find out a tool was
  OOM-killed rather than "slow".

---

## 5. Performance shape (measure, do not trust)

Order of magnitude only, to size the budgets. **Re-measure on the fixture corpus
before hardcoding anything** — and put the numbers back in this file.

| Operation | Rough cost | Implication |
|---|---|---|
| `pypdfium2` text extraction | very fast, hundreds of pages/sec | `find` over 500 pages is fine |
| `pdfplumber` word boxes | ~5–20 pages/sec | `extract_tables` wants a page range |
| Page render @150 DPI | tens of ms/page | fine; memory is the limit, not time |
| **Tesseract OCR** | **1–3 s/page/core** | **the hard limit — page-range scoped, always** |
| LibreOffice startup | seconds, once | keep a warm process or accept the cost |

OCR is the only operation whose cost forces a design change rather than a
parameter. Everything else is bounded by memory.

---

## 6. `pyproject.toml` starting point

```toml
requires-python = "==3.14.*"
dependencies = [
    "mcp>=1.0,<2.0",
    "pypdfium2>=4.30,<5.0",
    "pdfplumber>=0.11,<1.0",
    "pikepdf>=9.0,<10.0",
    "lxml>=5.0,<6.0",
    "python-docx>=1.1,<2.0",
    "openpyxl>=3.1,<4.0",
    "python-pptx>=1.0,<2.0",
    "charset-normalizer>=3.3,<4.0",
]
```

**Lock without `--upgrade`.** A sibling repo took a mystery `TypeError` in
production when scipy 1.18 changed what a string distribution name resolves to;
the pin was correct and the lock refresh was not.

---

## 7. Rejected, and why

| Rejected | Why |
|---|---|
| `unstructured` | pulls a large ML dependency tree and model weights; breaks offline and the container budget |
| `docling` | good quality, but downloads models — same problem |
| LayoutLM / Donut / Nougat / any VLM | GPU class of work; the founding constraint is CPU-only |
| `tabula-py` | requires a JVM |
| veraPDF (PDF/A validation) | Java. Means PDF/A *conversion* can ship without honest *validation*, so say so rather than claiming compliance |
| A vector store / RAG index | the design is just-in-time; `find` is a search over a document, not a corpus |
