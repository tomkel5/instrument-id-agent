# Instrument ID scheduled worker

This worker runs in the Instrument ID cluster. It uses the Instrument ID API key
and discovery state stored there; tracker issue processing belongs to `codex-agent`.

Each cycle first reads `GET /api/reparation-requests`. If there is an active
request, it processes only the first request using the existing listing from
`GET /api/listing/{listingId}` and the user's saved notes. It submits the result to
`PATCH /api/listing/{listingId}` and archives the request with
`DELETE /api/reparation-request/{id}?expectedUpdatedAt=...` only after the update
succeeds. Incomplete repairs and failures leave the request active. If the notes
changed during processing, archival returns 409 and the request remains active
for the next run.

If there are no active requests, the worker discovers and imports up to `AI_SEARCH_BATCH_SIZE` new
DIRECT listings (default `1`). A failed request lookup never falls back to discovery. After each
cycle the worker waits `DISCOVERY_INTERVAL_SECONDS` (default 600) before starting
another cycle, including when a repair fails or takes longer than ten minutes.

Before every new listing discovery run, the worker reads `GET /api/config` and
includes `AI_SEARCH_INSTRUCTIONS` in the Codex search prompt. Changes take effect
on the next discovery run without restarting the worker. Configured instructions
guide discovery while preserving evidence-based results, complete image galleries,
duplicate avoidance, and the `DIRECT_IMPORT_JSON` output contract. An empty or
whitespace-only value uses the default discovery instructions and logs at INFO;
a missing setting uses those defaults and logs a warning. A failed config lookup
or malformed response skips discovery for that cycle and logs the failure; the
next cycle retries. `AI_SEARCH_BATCH_SIZE` is read in the same request on every discovery run. Missing
batch size defaults to `1`; malformed or nonpositive values skip the cycle. For
larger batches Codex returns a JSON array; each candidate must satisfy the same
complete-gallery requirements. Only the first configured number of candidates
are processed. Previously imported sources and repeated URIs are skipped.

New discovery also reads every page of `GET /api/makers` to supply existing IDs,
names, and search terms to the search agent. An unavailable or malformed catalog
is discarded; discovery continues without assigning makers. Repairs do not read
the catalog. Search terms only aid identity lookup.

The agent must open the listing source and report instrument-specific maker
evidence, supporting quotations and URLs, attribution, confidence, plausible
alternatives, and reasoning in `makerAssessment`. Only verified, high-confidence
attributions with source-page evidence, no alternatives, and a unique catalog
name matching the proposed numeric ID are included as `makerId` in the DIRECT
import. Ambiguous attribution language in evidence also prevents assignment.
Seller names, snippets, image appearance, label text alone, and unverified
attributions cannot establish authorship. Missing assessments and unmatched or
ambiguous identities preserve the usual import with no maker ID.
`runs[].results[].makerDecision` records whether a maker was assigned and the
assessment and decision reason; assessment fields are excluded from API payloads.
The configured API credential is redacted from candidate and assessment state,
and catalog failures are logged without response bodies or exception details.

Each candidate outcome and discovered URI is saved atomically before the next
candidate is processed. Validation and import failures are recorded and do not
stop the remaining candidates. Successful imports and API duplicates enter
`sources`; failed candidates remain eligible for a later retry. `discoveredSources`
records all supplied source URIs and `runs[].results` records validation failures,
import failures, skipped duplicates, and each API response. Malformed JSON or a
missing output marker fails the cycle before any imports. Repairs neither read
nor use either discovery setting.

`INSTRUMENT_ID_API_URL` remains the full ingestion endpoint, for example
`http://instrument-id-ingester:8080/api/ingest`; repair endpoints are derived from
that service URL. All API calls use `INSTRUMENT_ID_API_KEY` as a bearer credential.

Deploy the listing service migration, then the ingester, then the UI and worker.
Run worker tests with `python -m unittest discover -s tests` from this repository.
