#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MCP_Documents — remote smoke test, all 13 tools across 2 sub-servers.
#
# NOT part of pytest / CI's unit jobs (CLAUDE.md §12). pytest never starts a
# server or touches the network; this drives real HTTP through real bearer auth
# against a real running container, with real documents, and checks the VALUES
# that come back rather than the success flag. It is also the only place
# LibreOffice and Tesseract are exercised at all -- the CI matrix runners have
# neither, so convert(to='pdf') and ocr() are tested here or nowhere.
#
# DOMAIN is required and has no default. The six sibling repos hard-code their
# deployment hostname here; this repo does not (CLAUDE.md §13 rule 14 --
# nothing in this tree names the deployment).
#
# Usage:
#   DOMAIN=http://localhost:8850 ./remote_smoke_test.sh        # a local container
#   DOMAIN=https://<host> ./remote_smoke_test.sh               # the deployment
#   DOCS_API_KEY=... DOMAIN=... ./remote_smoke_test.sh          # key inline
#   CONTAINER=mcp-documents ./remote_smoke_test.sh              # override name
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN (e.g. http://localhost:8850, or your deployment origin)}"
CONTAINER="${CONTAINER:-mcp-documents}"

# Read the key out of .env WITHOUT executing it. `source` runs every line, so a
# line that is not a KEY=VALUE assignment is a command -- that has already
# turned a stray summary line into a file named after a live secret. A plain
# read of one assignment cannot do that.
if [ -z "${DOCS_API_KEY:-}" ] && [ -f .env ]; then
  DOCS_API_KEY=$(sed -n 's/^[[:space:]]*DOCS_API_KEY[[:space:]]*=[[:space:]]*//p' .env | tail -n1 | tr -d '\042\047\r')
fi
KEY="${DOCS_API_KEY:?Set DOCS_API_KEY (env var or .env file) before running}"

D=/tmp/docs-smoke
HTML="$D/report.html"
TXT="$D/notes.txt"
PDF="$D/report.pdf"
SCAN="$D/scan.pdf"

FAILS=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }

# ── Reading values back out of the envelope ──────────────────────────────────
# A tool's document arrives as the JSON *string* result.content[0].text, so on
# the wire every key and value is escaped: \"pages\": 3. A pattern written for
# unescaped JSON matches nothing while every call still succeeds, which is how
# four of the six sibling repos silently stopped asserting anything after the
# official-SDK migration dropped structuredContent. `\\?` makes the escaping
# optional so these work either way.
#
# Every extractor ends `|| true`: under `set -o pipefail` a grep that matches
# nothing is a non-zero pipeline, and in a `VAR=$(...)` assignment that would
# abort the script before its own `|| fail` could report anything.
ok_json() { echo "$1" | grep -Eq '\\?"success\\?"[[:space:]]*:[[:space:]]*true'; }
is_true() { echo "$1" | grep -Eq "\\\\?\"$2\\\\?\"[[:space:]]*:[[:space:]]*true"; }
has_text() { echo "$1" | grep -qF "$2"; }
extract() {
  echo "$1" | grep -oE "\\\\?\"$2\\\\?\"[[:space:]]*:[[:space:]]*\\\\?\"[^\\\\\"]*" | head -1 |
    sed -E 's/.*"([^"]*)$/\1/' || true
}
extract_num() {
  echo "$1" | grep -oE "\\\\?\"$2\\\\?\"[[:space:]]*:[[:space:]]*-?[0-9]+" | head -1 |
    grep -oE -- '-?[0-9]+$' || true
}

echo "Target: $DOMAIN"

# Tools called with no explicit `out` default into MCP_OUTPUT_DIR, which on a
# real deployment is a directory the operator actually looks at. Remember what
# is there so this run can leave it exactly as it found it.
SHARED_DIR=$(docker exec "$CONTAINER" printenv MCP_OUTPUT_DIR 2>/dev/null || true)
SHARED_BEFORE=$(mktemp)
[ -n "$SHARED_DIR" ] && docker exec "$CONTAINER" sh -c "ls -1A '$SHARED_DIR' 2>/dev/null" | sort >"$SHARED_BEFORE"

echo
echo "== seed real documents into the container =="
# Real files, not stubs. The HTML carries two heading levels, a sentence with a
# distinctive phrase, and a genuine <table> -- so outline(), find(),
# extract_tables() and to_markdown() each have something true to find, and a
# tool that returns an empty answer cannot pass.
docker exec "$CONTAINER" mkdir -p "$D"
docker exec "$CONTAINER" python3 -c "
from pathlib import Path
Path('$HTML').write_text('''<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Quarterly Report</title></head><body>
<h1>Quarterly Report</h1>
<p>Revenue grew across every region in the first quarter.</p>
<h2>Regional Detail</h2>
<p>APAC led growth; EMEA held steady and AMER recovered late in the period.</p>
<table>
<tr><th>Region</th><th>Units</th><th>Revenue</th></tr>
<tr><td>APAC</td><td>120</td><td>1450.50</td></tr>
<tr><td>EMEA</td><td>95</td><td>1120.20</td></tr>
<tr><td>AMER</td><td>80</td><td>990.75</td></tr>
</table>
</body></html>
''', encoding='utf-8')
Path('$TXT').write_text('Internal note: the APAC figure excludes intercompany revenue.\n', encoding='utf-8')
"
# A page that is a picture of words and carries no text layer -- the input ocr()
# exists for. Drawn with a real TrueType face at a real size: the PIL bitmap
# default is ~11px and Tesseract reads nothing from it, so a fixture built that
# way would make ocr() 'pass' by recognising zero words.
docker exec "$CONTAINER" python3 -c "
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (1700, 2200), 'white')
draw = ImageDraw.Draw(img)
font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 72)
draw.text((150, 300), 'INVOICE 40521', fill='black', font=font)
draw.text((150, 500), 'Total due 1450.50', fill='black', font=font)
img.save('$SCAN', 'PDF', resolution=200.0)
"
docker exec "$CONTAINER" test -s "$HTML" && docker exec "$CONTAINER" test -s "$SCAN" &&
  pass "real HTML + note + a scanned page (no text layer) seeded" ||
  fail "seeding produced nothing"

declare -A SID
init_session() {
  curl -s -i -X POST "$DOMAIN/$1/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer $KEY" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' |
    grep -i mcp-session-id | tr -d '\r' | awk '{print $2}'
}
init_notified() {
  curl -s -X POST "$DOMAIN/$1/mcp" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer $KEY" -H "mcp-session-id: $2" \
    -d '{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}' >/dev/null
}

echo
echo "== auth enforcement =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$DOMAIN/read/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}')
[ "$code" = "401" ] && pass "no token -> 401" || fail "no token -> expected 401, got $code"

for tier in read edit; do
  SID[$tier]=$(init_session "$tier")
  init_notified "$tier" "${SID[$tier]}"
done
if [ -n "${SID[read]}" ] && [ -n "${SID[edit]}" ]; then
  pass "valid token -> sessions established on both sub-servers"
else
  fail "no session id (read='${SID[read]}' edit='${SID[edit]}')"
fi

call() {
  local tier="$1" id="$2" name="$3" args="$4"
  curl -s -X POST "$DOMAIN/$tier/mcp" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "Authorization: Bearer $KEY" -H "mcp-session-id: ${SID[$tier]}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"tools/call\",\"params\":{\"name\":\"$name\",\"arguments\":$args}}"
}

N=10
LAST_R=""
run() {
  local tier="$1" name="$2" args="$3" prompt="$4"
  echo "== prompt: \"$prompt\" -> $name =="
  N=$((N + 1))
  LAST_R=$(call "$tier" "$N" "$name" "$args")
  if ok_json "$LAST_R"; then pass "$name succeeded"; else fail "$name -> $LAST_R"; fi
}

# For a call whose CORRECT answer is a refusal. `run` reports success:false as
# a failure, which is right for the thirteen tools and wrong for the half-dozen
# checks below that exist to prove a refusal happens -- using it there printed
# a FAIL immediately above the PASS that judged the same response.
expect() {
  local tier="$1" name="$2" args="$3" prompt="$4"
  echo "== prompt: \"$prompt\" -> $name (a refusal is the right answer) =="
  N=$((N + 1))
  LAST_R=$(call "$tier" "$N" "$name" "$args")
}

echo
echo "===== docs-edit: convert (LibreOffice) makes the PDF everything else uses ====="
run edit convert "{\"source\":\"$HTML\",\"to\":\"pdf\",\"out\":\"$PDF\"}" "turn the report into a PDF"
PDF_PAGES=$(extract_num "$LAST_R" pages)
if [ -n "$PDF_PAGES" ] && [ "$PDF_PAGES" -ge 1 ]; then
  pass "LibreOffice produced a $PDF_PAGES-page PDF"
else
  fail "convert(to='pdf') reported no page count -- LibreOffice missing or it wrote an empty document"
fi
docker exec "$CONTAINER" test -s "$PDF" &&
  pass "the PDF is a real non-empty file on disk, not just a success message" ||
  fail "no file at $PDF inside the container"

echo
echo "===== docs-read: 7 tools ====="
run read probe "{\"source\":\"$PDF\"}" "what is this document?"
PROBE_PAGES=$(extract_num "$LAST_R" pages)
EXTRACTABLE=$(extract "$LAST_R" extractable)
[ "$PROBE_PAGES" = "$PDF_PAGES" ] &&
  pass "probe agrees with convert on the page count ($PROBE_PAGES)" ||
  fail "convert said $PDF_PAGES pages, probe says $PROBE_PAGES"
[ "$EXTRACTABLE" = "full" ] &&
  pass "probe read a full text layer, as a LibreOffice PDF must have" ||
  fail "extractable='$EXTRACTABLE', expected 'full' on a born-digital PDF"

run read outline "{\"source\":\"$HTML\"}" "what are the headings?"
# Against the HTML rather than the PDF: the source has real <h1>/<h2> tags, so
# a correct answer is knowable exactly. A PDF has no headings, only font sizes,
# and asserting on an inference would pass for the wrong reason.
has_text "$LAST_R" "Regional Detail" &&
  pass "outline found the real h2 heading" ||
  fail "outline missed 'Regional Detail' -> $LAST_R"

run read find "{\"source\":\"$PDF\",\"query\":\"Revenue\"}" "where does the report mention Revenue?"
HITS=$(extract_num "$LAST_R" hits)
[ -n "$HITS" ] && [ "$HITS" -ge 1 ] &&
  pass "find located $HITS occurrence(s) of a phrase that is really there" ||
  fail "find reported $HITS hits for text the document contains"

run read extract "{\"source\":\"$PDF\"}" "read the report"
has_text "$LAST_R" "APAC led growth" &&
  pass "extract returned the seeded sentence, not an empty string" ||
  fail "extract did not return the text that is in the document"

run read extract_tables "{\"source\":\"$PDF\"}" "get the table out of the report"
TABLES=$(extract_num "$LAST_R" count)
[ -n "$TABLES" ] && [ "$TABLES" -ge 1 ] &&
  pass "extract_tables found $TABLES table(s)" ||
  fail "extract_tables found $TABLES tables in a document with one"
has_text "$LAST_R" "1450.50" &&
  pass "the table's cell values came back, not just its shape" ||
  fail "table rows did not contain the seeded value 1450.50"

run read read_page "{\"source\":\"$PDF\",\"page\":1}" "read page 1"
has_text "$LAST_R" "Quarterly Report" &&
  pass "read_page returned page 1's real text" ||
  fail "read_page returned no recognisable content"

run read to_markdown "{\"source\":\"$HTML\"}" "give me the report as markdown"
has_text "$LAST_R" "# Quarterly Report" &&
  pass "to_markdown emitted a real heading, not flattened prose" ||
  fail "to_markdown produced no '# ' heading from an h1"
has_text "$LAST_R" "## Regional Detail" &&
  pass "the heading LEVEL came from the tag, not from a font size" ||
  fail "the h2 did not come back as '## '"
# A tool named to_markdown that emits a tab-separated flattening of a <table>
# has answered with something that is not markdown.
has_text "$LAST_R" "| Region | Units | Revenue |" &&
  pass "the declared table came back as a markdown table" ||
  fail "the table was not rendered as markdown"
MD_BASIS=$(extract "$LAST_R" basis)
[ "$MD_BASIS" = "native" ] &&
  pass "basis says 'native' — the document declared this, it was not inferred" ||
  fail "basis='$MD_BASIS' for a format that declares its own headings"

echo
echo "===== docs-read against a format that is not PDF ====="
# The whole claim of core/readers is that one set of tools serves every format.
# A plain .txt has no pages of its own, so this also exercises the synthetic
# pagination path that only flow formats take.
run read probe "{\"source\":\"$TXT\"}" "what is this text file?"
PAGINATION=$(extract "$LAST_R" pagination)
[ "$PAGINATION" = "synthetic" ] &&
  pass "a page-less format is paginated synthetically and says so" ||
  fail "pagination='$PAGINATION' for a .txt, expected 'synthetic'"

echo
echo "===== a bundle: the archive manifest, and the member inside it ====="
# Only reachable from here. Reading a member EXTRACTS it into the container's
# inbox as uid 999; a permissions or path fault there is invisible to pytest,
# which runs as the owner on a host filesystem.
docker exec "$CONTAINER" python3 -c "
import zipfile
with zipfile.ZipFile('$D/bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write('$HTML', 'report.html')
    z.writestr('facts.xbrl', '''<?xml version=\"1.0\"?>
<xbrl xmlns=\"http://www.xbrl.org/2003/instance\">
  <context id=\"Q1\"><entity><identifier scheme=\"s\">ACME</identifier></entity>
    <period><instant>2026-03-31</instant></period></context>
  <unit id=\"u\"><measure>iso4217:IDR</measure></unit>
  <Assets contextRef=\"Q1\" unitRef=\"u\" decimals=\"0\">1640830566000000</Assets>
</xbrl>''')
"
run read probe "{\"source\":\"$D/bundle.zip\"}" "what is in this archive?"
ZIP_FORMAT=$(extract "$LAST_R" format)
[ "$ZIP_FORMAT" = "zip" ] &&
  pass "an archive opens as itself, not as one of its members" ||
  fail "format='$ZIP_FORMAT' for a .zip, expected 'zip'"
echo "$LAST_R" | grep -q 'facts.xbrl' &&
  pass "the manifest lists the members" ||
  fail "probe of an archive did not list facts.xbrl"
echo "$LAST_R" | grep -q 'open_with' &&
  pass "the listing says how to open a member" ||
  fail "no open_with in the listing -- a manifest with no way in is a dead end"

run read probe "{\"source\":\"$D/bundle.zip::facts.xbrl\"}" "read the XBRL inside the bundle"
MEMBER_FORMAT=$(extract "$LAST_R" format)
[ "$MEMBER_FORMAT" = "xbrl" ] &&
  pass "the member is read as an XBRL, not as an archive" ||
  fail "format='$MEMBER_FORMAT' for a member, expected 'xbrl'"
MEMBER_BASIS=$(extract "$LAST_R" basis)
[ "$MEMBER_BASIS" = "native" ] &&
  pass "tagged facts are reported native, not text_layer" ||
  fail "basis='$MEMBER_BASIS' for tagged XBRL facts, expected 'native'"

# The filed figure has to survive the round trip exactly. An instance states
# full rupiah where the PDF beside it prints millions, and any rescaling here
# would produce a number matching neither document.
run read find "{\"source\":\"$D/bundle.zip::facts.xbrl\",\"query\":\"1640830566000000\"}" \
  "find the filed figure in the member"
MEMBER_HITS=$(extract_num "$LAST_R" hits)
[ "${MEMBER_HITS:-0}" -ge 1 ] &&
  pass "the filed value is returned exactly, unrescaled ($MEMBER_HITS hit)" ||
  fail "the filed figure was not found in the member"

expect read probe "{\"source\":\"$D/bundle.zip::../../etc/passwd\"}" "escape the archive"
echo "$LAST_R" | grep -q 'safe member' &&
  pass "a member path that walks upward is refused" ||
  fail "a '..' member name was not refused"

echo
echo "===== docs-edit: the remaining 5 tools ====="
run edit assemble "{\"sources\":[\"$PDF\",\"$PDF\"],\"select\":\"s0:all, s1:1r90\",\"out\":\"$D/merged.pdf\"}" \
  "merge the report with itself and rotate the first page of the copy"
WRITTEN=$(extract_num "$LAST_R" pages_written)
EXPECTED=$((PDF_PAGES + 1))
[ "$WRITTEN" = "$EXPECTED" ] &&
  pass "assemble wrote $WRITTEN pages, which is all of it plus one" ||
  fail "assemble wrote $WRITTEN pages, expected $EXPECTED"

run edit optimize "{\"source\":\"$PDF\",\"action\":\"compress\",\"out\":\"$D/small.pdf\"}" "compress the report"
BEFORE=$(extract_num "$LAST_R" bytes_before)
AFTER=$(extract_num "$LAST_R" bytes_after)
if [ -n "$BEFORE" ] && [ -n "$AFTER" ] && [ "$BEFORE" -gt 0 ] && [ "$AFTER" -gt 0 ]; then
  pass "optimize reported real byte counts on both sides ($BEFORE -> $AFTER)"
else
  fail "optimize reported bytes_before='$BEFORE' bytes_after='$AFTER'"
fi

run edit protect "{\"source\":\"$PDF\",\"action\":\"encrypt\",\"password\":\"smoke-pw\",\"out\":\"$D/locked.pdf\"}" \
  "put a password on the report"
is_true "$LAST_R" encrypted && pass "protect reports the file is encrypted" || fail "protect did not report encrypted"
# The claim is only worth what a reader says about it: probe WITHOUT the
# password must refuse, and WITH it must work. Either half alone can pass while
# the file is untouched.
expect read probe "{\"source\":\"$D/locked.pdf\"}" "read the locked file with no password"
if ok_json "$LAST_R"; then
  fail "probe read an encrypted document with no password"
else
  pass "an encrypted document is refused without its password"
fi
run read probe "{\"source\":\"$D/locked.pdf\",\"password\":\"smoke-pw\"}" "read it with the password"

run edit redact "{\"source\":\"$PDF\",\"pattern\":\"1450.50\",\"out\":\"$D/redacted.pdf\"}" \
  "permanently remove the APAC revenue figure"
REDACTED=$(extract_num "$LAST_R" redacted)
[ -n "$REDACTED" ] && [ "$REDACTED" -ge 1 ] &&
  pass "redact removed $REDACTED run(s) of text that was really there" ||
  fail "redact removed $REDACTED runs -- a redaction that matched nothing proves nothing"
is_true "$LAST_R" verified &&
  pass "redact verified the pattern is no longer extractable" ||
  fail "redact did not verify its own work"
# And verified independently, by a different tool reading the file back. A tool
# checking its own output is the weaker half of this.
run read find "{\"source\":\"$D/redacted.pdf\",\"query\":\"1450.50\"}" "is the figure still findable?"
RESIDUAL=$(extract_num "$LAST_R" hits)
[ "$RESIDUAL" = "0" ] &&
  pass "find confirms the redacted value is gone from the file" ||
  fail "find still located the 'redacted' value $RESIDUAL time(s)"

run edit ocr "{\"source\":\"$SCAN\",\"out\":\"$D/scanned_ocr.pdf\"}" "make the scan searchable"
OCRED=$(extract_num "$LAST_R" pages_ocred)
[ -n "$OCRED" ] && [ "$OCRED" -ge 1 ] &&
  pass "ocr processed $OCRED page(s)" ||
  fail "ocr processed $OCRED pages -- Tesseract missing, or it skipped the page"
# The only assertion that proves OCR did anything: the words are findable in
# the output. pages_ocred counts what was attempted, not what was recognised.
run read find "{\"source\":\"$D/scanned_ocr.pdf\",\"query\":\"INVOICE\"}" "find INVOICE in the now-searchable scan"
OCR_HITS=$(extract_num "$LAST_R" hits)
[ -n "$OCR_HITS" ] && [ "$OCR_HITS" -ge 1 ] &&
  pass "the OCR text layer is real: a word from the image is findable" ||
  fail "nothing findable in the OCR output -- the text layer was not embedded"

echo
echo "===== refusals are refusals, not crashes ====="
# A tool that only ever gets valid input is a tool whose error path has never
# run. Both of these are documented behaviour, and both must come back as a
# structured refusal with a hint rather than an exception.
N=$((N + 1))
R=$(call edit "$N" optimize "{\"source\":\"$HTML\",\"action\":\"compress\"}")
if ok_json "$R"; then
  fail "optimize accepted an HTML file, which is not a PDF"
elif has_text "$R" "convert("; then
  pass "a non-PDF to a PDF-only tool is refused with the convert() call that fixes it"
else
  fail "optimize refused an HTML file without naming the fix -> $R"
fi
N=$((N + 1))
R=$(call edit "$N" convert "{\"source\":\"$PDF\",\"to\":\"docx\"}")
if ok_json "$R"; then
  fail "convert claimed to reconstruct a PDF into docx"
elif has_text "$R" "extract_tables"; then
  pass "pdf -> docx is refused, and the refusal names what the caller can have instead"
else
  fail "pdf -> docx refusal did not offer an alternative -> $R"
fi

echo
echo "===== hybrid file exchange (remote-only behaviour) ====="
if [ -z "$SHARED_DIR" ]; then
  echo "  SKIP: MCP_OUTPUT_DIR is unset on $CONTAINER — nothing to verify"
else
  echo "== prompt: \"convert the note to markdown\" -> convert with no out =="
  N=$((N + 1))
  EX_R=$(call edit "$N" convert "{\"source\":\"$TXT\",\"to\":\"md\"}")
  EX_PATH=$(extract "$EX_R" out)
  EX_URL=$(extract "$EX_R" public_url)
  case "$EX_PATH" in
  "$SHARED_DIR"/*) pass "default output landed in the shared dir ($EX_PATH)" ;;
  *) fail "default output went to '$EX_PATH', expected it under $SHARED_DIR" ;;
  esac
  [ -n "$EX_URL" ] && pass "response carried public_url ($EX_URL)" || fail "no public_url in response"
  if docker exec "$CONTAINER" test -s "$EX_PATH"; then
    pass "the markdown is a real non-empty file on disk"
  else
    fail "no file at $EX_PATH inside the container"
  fi
  MODE=$(docker exec "$CONTAINER" stat -c '%a' "$EX_PATH" 2>/dev/null)
  case "$MODE" in
  *[4567]) pass "generated file is readable by the file server sharing the dir (mode $MODE)" ;;
  *) fail "mode $MODE leaves the file unreadable to anything else sharing the directory" ;;
  esac
  docker exec "$CONTAINER" rm -f "$EX_PATH"

  echo "== prompt: \"read the document at <link>\" -> a URL as a source =="
  # A *sibling* endpoint's public /health, never this server's own: fetching
  # its own public URL deadlocks, because the tool call occupies the worker
  # that would have to serve the request.
  N=$((N + 1))
  URL_R=$(call read "$N" probe '{"source":"https://raw.githubusercontent.com/astral-sh/uv/main/README.md"}')
  if has_text "$URL_R" "does not fetch URLs" || has_text "$URL_R" "URL fetching is off"; then
    echo "  SKIP: MCP_FETCH_URLS is not enabled on $CONTAINER"
  elif ok_json "$URL_R"; then
    pass "a URL was accepted as a source and fetched server-side"
  else
    fail "URL input -> $URL_R"
  fi

  echo "== SSRF guard: a private address must be refused =="
  N=$((N + 1))
  SSRF_R=$(call read "$N" probe '{"source":"http://169.254.169.254/latest/meta-data/"}')
  if has_text "$SSRF_R" "non-public address"; then
    pass "link-local metadata address refused"
  elif has_text "$SSRF_R" "does not fetch URLs" || has_text "$SSRF_R" "URL fetching is off"; then
    echo "  SKIP: URL fetching disabled, guard not reachable"
  else
    fail "SSRF guard did not fire -> $SSRF_R"
  fi
fi

echo
echo "== leave the container as we found it =="
docker exec "$CONTAINER" rm -rf "$D"
if [ -n "$SHARED_DIR" ]; then
  docker exec "$CONTAINER" sh -c "ls -1A '$SHARED_DIR' 2>/dev/null" | sort |
    comm -13 "$SHARED_BEFORE" - |
    while IFS= read -r leftover; do
      [ -n "$leftover" ] && docker exec "$CONTAINER" rm -rf "$SHARED_DIR/$leftover"
    done
  pass "removed everything this run added to $SHARED_DIR"
fi
rm -f "$SHARED_BEFORE"

echo
if [ "$FAILS" -eq 0 ]; then
  echo "ALL 13 TOOLS PASSED against $DOMAIN"
else
  echo "$FAILS CHECK(S) FAILED against $DOMAIN"
  exit 1
fi
