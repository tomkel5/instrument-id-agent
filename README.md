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

If there are no active requests, the worker discovers and imports at most one new
DIRECT listing. A failed request lookup never falls back to discovery. After each
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
next cycle retries. Repairs neither read nor use this setting.

`INSTRUMENT_ID_API_URL` remains the full ingestion endpoint, for example
`http://instrument-id-ingester:8080/api/ingest`; repair endpoints are derived from
that service URL. All API calls use `INSTRUMENT_ID_API_KEY` as a bearer credential.

Deploy the listing service migration, then the ingester, then the UI and worker.
Run worker tests with `python -m unittest discover -s tests` from this repository.
