"""Periodic Instrument ID discovery and repair worker."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

LOG = logging.getLogger(__name__)


def config() -> dict[str, object]:
    interval = float(os.environ.get("DISCOVERY_INTERVAL_SECONDS", "600"))
    if interval <= 0:
        raise RuntimeError("DISCOVERY_INTERVAL_SECONDS must be greater than zero")
    return {
        "interval": interval,
        "api_url": os.environ.get("INSTRUMENT_ID_API_URL", "http://instrument-id-ingester:8080/api/ingest").rstrip("/"),
        "api_key": os.environ.get("INSTRUMENT_ID_API_KEY", ""),
        "state_dir": Path(os.environ.get("STATE_DIR", "/var/lib/instrument-id-agent")),
        "workspace": Path(os.environ.get("WORKSPACE_DIR", "/workspace")),
        "codex_command": ("codex", "--search", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-"),
    }


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"sources": [], "runs": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"sources": [], "runs": []}
    except json.JSONDecodeError:
        LOG.warning("Ignoring invalid state file %s", path)
        return {"sources": [], "runs": []}


def prompt(state: dict[str, object], search_instructions: str = "", batch_size: int = 1) -> str:
    configured_instructions = (
        "\nConfigured discovery instructions (AI_SEARCH_INSTRUCTIONS):\n"
        + search_instructions
        + "\nThese instructions guide discovery; always preserve the evidence, complete-gallery, "
        "duplicate-avoidance, and output requirements below.\n"
        if search_instructions.strip() else ""
    )
    output_format = (
        "a single JSON object" if batch_size == 1 else
        f"a JSON array of up to {batch_size} objects with distinct source URIs (never repeat a listing)"
    )
    return f"""Find up to {batch_size} distinct suitable publicly accessible web pages, each featuring a specific violin that is likely not already in the Instrument ID database.
{configured_instructions}

Use live web search. Prefer a page from a dealer, maker, auction house, or private owner over a museum or library collection. Do not invent facts, URLs, or image URLs. Open the source page and inspect its complete image gallery, including gallery markup or linked image resources when necessary. Collect every unique image belonging to this exact instrument; do not stop after the first one or two images. If the page says the gallery contains N images, verify that your output contains all N usable image URLs, or explain why a specific image cannot be used. Avoid every source URL in the sources list below. Failed candidates recorded in runs or discoveredSources may be retried.

Previous discovery state:
{json.dumps(state, indent=2, sort_keys=True)}

If you find a suitable violin, finish with exactly one line beginning with DIRECT_IMPORT_JSON: followed by {output_format} with these fields for each listing:
{{"uri":"source page URL","title":"listing title","description":"evidence-based description","imageUrls":["absolute image URL"]}}

The source page must be the URI. Include only directly usable HTTP or HTTPS image URLs. Before responding, compare the output list with the page's complete gallery and remove duplicates while retaining every distinct view. If no suitable candidate can be found, finish with exactly: DIRECT_IMPORT_JSON: null.
"""


def candidate(output: str, allow_empty_images: bool = False) -> dict[str, object] | None:
    match = re.search(r"^DIRECT_IMPORT_JSON:\s*(.+)$", output, re.IGNORECASE | re.MULTILINE)
    if not match:
        raise RuntimeError("Codex output did not include DIRECT_IMPORT_JSON")
    value = json.loads(match.group(1))
    if value is None:
        return None
    return validate_candidate(value, allow_empty_images)


def validate_candidate(value: object, allow_empty_images: bool = False) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("DIRECT_IMPORT_JSON must be an object or null")
    for key in ("uri", "title", "description"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise RuntimeError(f"DIRECT_IMPORT_JSON is missing {key}")
    images = value.get("imageUrls")
    if not isinstance(images, list) or (not images and not allow_empty_images) or any(not isinstance(item, str) or not item.startswith(("http://", "https://")) for item in images):
        raise RuntimeError("DIRECT_IMPORT_JSON must contain HTTP image URLs")
    value["imageUrls"] = list(dict.fromkeys(images))
    return value


def import_direct(settings: dict[str, object], value: dict[str, object]) -> object:
    if not settings["api_key"]:
        raise RuntimeError("INSTRUMENT_ID_API_KEY is not configured")
    request = urllib.request.Request(
        str(settings["api_url"]),
        data=json.dumps({**value, "type": "DIRECT"}).encode("utf-8"),
        headers={"Accept": "application/json", "Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[-2000:]
        if error.code == 409:
            return {"duplicate": True, "response": body}
        raise RuntimeError(f"DIRECT import failed with HTTP {error.code}: {body}") from error


def api_request(settings: dict[str, object], path: str, method: str = "GET", value: object = None) -> object:
    if not settings["api_key"]:
        raise RuntimeError("INSTRUMENT_ID_API_KEY is not configured")
    base = str(settings["api_url"]).removesuffix("/api/ingest")
    request = urllib.request.Request(
        base + "/api" + path,
        data=json.dumps(value).encode("utf-8") if value is not None else None,
        headers={"Accept": "application/json", "Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
        return json.loads(body) if body else None


def repair_prompt(request: dict[str, object], listing: object) -> str:
    return f"""Repair exactly this existing DIRECT listing. Use live web search and open its source page.
Existing listing: {json.dumps(listing)}
User repair instructions: {json.dumps(request["notes"])}
Follow the instructions using evidence from the source. Inspect the complete image gallery and collect every unique image of this exact instrument. Do not invent facts or URLs. Preserve correct existing metadata. Images are additive: the API retains existing images and ignores duplicate URLs. Do not discover or import another listing.
When the requested repair can be completed, finish with DIRECT_IMPORT_JSON: followed by a single JSON object:
{{"uri":"original source URL","title":"correct listing title","description":"evidence-based description","imageUrls":["absolute HTTP image URL"]}}
For a metadata-only repair imageUrls may be empty. If you cannot complete the instructions, finish with DIRECT_IMPORT_JSON: null; the request will remain active.
"""


def discovery_config(settings: dict[str, object]) -> tuple[str, int]:
    try:
        values = api_request(settings, "/config")
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError("Unable to read discovery configuration; skipping discovery this cycle") from error
    if not isinstance(values, list):
        raise RuntimeError("Config API did not return a list; skipping discovery this cycle")
    batch_size = 1
    for value in values:
        if isinstance(value, dict) and value.get("name") == "AI_SEARCH_BATCH_SIZE":
            raw = value.get("value")
            if not isinstance(raw, str) or not re.fullmatch(r"[0-9]*[1-9][0-9]*", raw):
                raise RuntimeError("AI_SEARCH_BATCH_SIZE must be a positive integer; skipping discovery this cycle")
            batch_size = int(raw)
    for value in values:
        if isinstance(value, dict) and value.get("name") == "AI_SEARCH_INSTRUCTIONS":
            instructions = value.get("value")
            if not isinstance(instructions, str):
                raise RuntimeError("AI_SEARCH_INSTRUCTIONS is not a string; skipping discovery this cycle")
            if not instructions.strip():
                LOG.info("AI_SEARCH_INSTRUCTIONS is empty; using default discovery instructions")
            return instructions, batch_size
    LOG.warning("AI_SEARCH_INSTRUCTIONS is missing; using default discovery instructions")
    return "", batch_size


def discovery_instructions(settings: dict[str, object]) -> str:
    return discovery_config(settings)[0]


def save_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def discover_results(settings: dict[str, object], state: dict[str, object], path: Path,
                     output: str, batch_size: int, run: dict[str, object]) -> None:
    match = re.search(r"^DIRECT_IMPORT_JSON:\s*(.+)$", output, re.IGNORECASE | re.MULTILINE)
    if not match:
        raise RuntimeError("Codex output did not include DIRECT_IMPORT_JSON")
    payload = json.loads(match.group(1))
    values = payload if isinstance(payload, list) else ([] if payload is None else [payload])
    outcomes = []
    run["results"] = outcomes
    run["result"] = "no-candidate" if not values else "batch-completed"
    runs = state.setdefault("runs", [])
    runs.append(run)
    del runs[:-100]
    seen = set(state.setdefault("sources", []))
    for raw in values[:batch_size]:
        outcome = {"candidate": raw}
        outcomes.append(outcome)
        uri = raw.get("uri") if isinstance(raw, dict) else None
        discovered = state.setdefault("discoveredSources", [])
        if isinstance(uri, str) and uri.strip() and uri not in discovered:
            discovered.append(uri)
        try:
            value = validate_candidate(raw)
        except (ValueError, RuntimeError) as error:
            outcome.update(result="validation-failed", error=str(error))
        else:
            uri = value["uri"]
            if uri in seen:
                outcome["result"] = "duplicate-source"
            else:
                seen.add(uri)
                try:
                    response = import_direct(settings, value)
                except (OSError, ValueError, RuntimeError) as error:
                    outcome.update(result="import-failed", error=str(error))
                else:
                    outcome["response"] = response
                    outcome["result"] = "duplicate" if isinstance(response, dict) and response.get("duplicate") else "imported"
                    state["sources"].append(uri)
        # Persist each outcome before processing the next candidate.
        save_state(path, state)
    if len(outcomes) == 1:
        run.update(outcomes[0])
    save_state(path, state)
    LOG.info("Discovery completed: %s", run["result"])


def run_once(settings: dict[str, object]) -> None:
    state_path = Path(settings["state_dir"]) / "discovery-state.json"
    state = load_state(state_path)
    requests = api_request(settings, "/reparation-requests")
    if not isinstance(requests, list):
        raise RuntimeError("Reparation request API did not return a list")
    repair = requests[0] if requests else None
    listing = api_request(settings, f"/listing/{repair['listingId']}") if repair else None
    if repair:
        task_prompt = repair_prompt(repair, listing)
    else:
        instructions, batch_size = discovery_config(settings)
        task_prompt = prompt(state, instructions, batch_size)
    with tempfile.TemporaryDirectory(dir=settings["workspace"]) as directory:
        result = subprocess.run(
            list(settings["codex_command"]), cwd=directory, input=task_prompt, text=True,
            capture_output=True, check=False,
        )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or f"Codex exited with {result.returncode}")
    run: dict[str, object] = {"completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if repair is None:
        discover_results(settings, state, state_path, result.stdout, batch_size, run)
        return
    value = candidate(result.stdout, allow_empty_images=True)
    if repair is not None:
        run["requestId"] = repair["id"]
        if value is None:
            run["result"] = "repair-incomplete"
        else:
            run["response"] = api_request(settings, f"/listing/{repair['listingId']}", "PATCH",
                                           {key: value[key] for key in ("title", "description", "imageUrls")})
            query = urlencode({"expectedUpdatedAt": repair["updatedAt"]})
            api_request(settings, f"/reparation-request/{repair['id']}?{query}", "DELETE")
            run["result"] = "repaired"
    runs = state.setdefault("runs", [])
    if isinstance(runs, list):
        runs.append(run)
        del runs[:-100]
    save_state(state_path, state)
    LOG.info("Discovery completed: %s", run["result"])


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = config()
    Path(settings["workspace"]).mkdir(parents=True, exist_ok=True)
    LOG.info("Instrument ID agent started; interval=%ss", settings["interval"])
    while True:
        try:
            run_once(settings)
        except Exception:
            LOG.exception("Discovery cycle failed")
        time.sleep(float(settings["interval"]))


if __name__ == "__main__":
    main()
