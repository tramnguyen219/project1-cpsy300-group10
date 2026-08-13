"""
Phase 3: caching + data interaction.

The expensive work — cleaning the CSV and computing every chart aggregate —
runs exactly once, in the blob trigger, when All_Diets.csv changes. It writes
two artefacts back to blob storage:

    cleaned_diets.csv      cleaned rows, serves recipe search
    dashboard_results.json precomputed chart payloads, serves the dashboard

The HTTP endpoints only ever read those. A dashboard load never parses the raw
CSV and never recomputes an aggregate, which is what separates this from the
Phase 2 function that did the full pipeline on every single request.

Response shapes are pinned by dashboard/API-CONTRACT.md — the frontend is
already written against them.
"""

import io
import json
import logging
import os
import time
from datetime import datetime, timezone

import azure.functions as func
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from auth_core import current_user

bp = func.Blueprint()

CONTAINER = "diets-dataset"
RAW_BLOB = "All_Diets.csv"
CLEANED_BLOB = "cleaned_diets.csv"
RESULT_BLOB = "dashboard_results.json"

MACROS = ["Protein(g)", "Carbs(g)", "Fat(g)"]
TEXT_COLS = ["Diet_type", "Recipe_name", "Cuisine_type"]

# The scatter chart is illustrative, not exhaustive — sending all 7806 rows
# would bloat the payload and the browser cannot draw them distinguishably.
SCATTER_POINTS = 300
MAX_PAGE_SIZE = 100

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _service() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"])


def _blob(name: str):
    return _service().get_blob_client(container=CONTAINER, blob=name)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _respond(payload: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status,
                             mimetype="application/json", headers=CORS)


def _fail(msg: str, status: int = 400) -> func.HttpResponse:
    return _respond({"error": msg}, status)


def _authorised(req: func.HttpRequest) -> bool:
    """The dashboard is only visible to a signed-in user, so its data is too."""
    return current_user(req) is not None


# --------------------------------------------------------------------------
# the work that must happen only once per file change
# --------------------------------------------------------------------------
def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate, coerce the macros to numbers, fill gaps with the column mean."""
    df = df.drop_duplicates()

    for col in TEXT_COLS:
        df[col] = df[col].astype(str).str.strip()
    for col in ("Diet_type", "Cuisine_type"):
        df[col] = df[col].str.lower()

    df[MACROS] = df[MACROS].apply(pd.to_numeric, errors="coerce")
    df[MACROS] = df[MACROS].fillna(df[MACROS].mean())

    # A recipe with no name is unusable in search results.
    df = df[df["Recipe_name"].str.len() > 0]
    return df.reset_index(drop=True)


def build_results(df: pd.DataFrame, source_version: str, elapsed_ms: float) -> dict:
    """Every number the four charts need, computed once."""
    avg = df.groupby("Diet_type")[MACROS].mean().round(2).reset_index()
    counts = (df.groupby("Diet_type").size()
                .reset_index(name="count").sort_values("Diet_type"))
    corr = df[MACROS].corr().round(3)

    sample = df.sample(min(SCATTER_POINTS, len(df)), random_state=42)

    return {
        "meta": {
            "cache_hit": False,          # this response *is* the recompute
            "computed_at": _utc_now(),
            "served_at": _utc_now(),
            "execution_time_ms": round(elapsed_ms, 2),
            "source_version": source_version,
            "row_count": int(len(df)),
        },
        "avg_macros_per_diet": avg.to_dict(orient="records"),
        "protein_vs_carbs": sample[["Diet_type", "Recipe_name"] + MACROS[:2]]
                                  .to_dict(orient="records"),
        "macro_correlation": {"labels": MACROS, "matrix": corr.values.tolist()},
        "recipe_count_per_diet": counts.to_dict(orient="records"),
        "diet_types": sorted(df["Diet_type"].unique().tolist()),
    }


def rebuild(raw_bytes: bytes, source_version: str) -> dict:
    """Clean, aggregate, and persist both artefacts. Returns the results payload."""
    started = time.time()

    df = clean(pd.read_csv(io.BytesIO(raw_bytes)))

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    _blob(CLEANED_BLOB).upload_blob(buf.getvalue().encode(), overwrite=True)

    results = build_results(df, source_version, (time.time() - started) * 1000)
    _blob(RESULT_BLOB).upload_blob(json.dumps(results).encode(), overwrite=True)

    logging.info("Rebuilt cache from %s: %d rows in %.0f ms",
                 RAW_BLOB, len(df), results["meta"]["execution_time_ms"])
    return results


@bp.blob_trigger(arg_name="raw",
                 path=f"{CONTAINER}/{RAW_BLOB}",
                 connection="AZURE_STORAGE_CONNECTION_STRING")
def ProcessDietsFile(raw: func.InputStream) -> None:
    """
    Fires only when All_Diets.csv is created or updated. This is the single
    place cleaning and result calculation happen — upload a new version of the
    file and this runs once; every dashboard request afterwards is served from
    what it wrote.
    """
    logging.info("Blob trigger fired for %s (%s bytes)", raw.name, raw.length)
    rebuild(raw.read(), source_version=_utc_now())


# --------------------------------------------------------------------------
# read-only endpoints
# --------------------------------------------------------------------------
@bp.route(route="GetDashboardResults", methods=["GET", "OPTIONS"],
          auth_level=func.AuthLevel.ANONYMOUS)
def GetDashboardResults(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=CORS)
    if not _authorised(req):
        return _fail("Unauthorized", 401)

    started = time.time()
    try:
        cached = _blob(RESULT_BLOB).download_blob().readall()
        results = json.loads(cached)
        results["meta"]["cache_hit"] = True

    except ResourceNotFoundError:
        # Nothing has changed All_Diets.csv since deployment, so the trigger has
        # never fired. Build it once now rather than serving a broken dashboard.
        logging.warning("%s missing — building it on first request", RESULT_BLOB)
        try:
            raw = _blob(RAW_BLOB).download_blob().readall()
        except ResourceNotFoundError:
            return _fail(f"{RAW_BLOB} not found in container '{CONTAINER}'.", 404)
        results = rebuild(raw, source_version="initial")

    except Exception as exc:
        logging.exception("GetDashboardResults failed")
        return _fail(str(exc), 500)

    results["meta"]["served_at"] = _utc_now()
    results["meta"]["execution_time_ms"] = round((time.time() - started) * 1000, 2)
    return _respond(results)


# Warm instances reuse the parsed frame; the etag check means a rebuilt cache
# is picked up on the next request rather than being served stale.
_recipes = {"etag": None, "df": None}


def _cleaned_frame() -> pd.DataFrame:
    client = _blob(CLEANED_BLOB)
    etag = client.get_blob_properties().etag

    if _recipes["etag"] != etag:
        _recipes["df"] = pd.read_csv(io.BytesIO(client.download_blob().readall()))
        _recipes["etag"] = etag
        logging.info("Loaded %s into memory (%d rows)", CLEANED_BLOB, len(_recipes["df"]))

    return _recipes["df"]


@bp.route(route="GetRecipes", methods=["GET", "OPTIONS"],
          auth_level=func.AuthLevel.ANONYMOUS)
def GetRecipes(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=CORS)
    if not _authorised(req):
        return _fail("Unauthorized", 401)

    keyword = (req.params.get("q") or "").strip().lower()
    diet = (req.params.get("diet_type") or "").strip().lower()

    try:
        page = max(1, int(req.params.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(req.params.get("page_size", 10))))
    except ValueError:
        return _fail("page and page_size must be integers.")

    try:
        df = _cleaned_frame()
    except ResourceNotFoundError:
        return _fail(f"{CLEANED_BLOB} does not exist yet — update {RAW_BLOB} to build it.", 404)
    except Exception as exc:
        logging.exception("GetRecipes failed")
        return _fail(str(exc), 500)

    if diet:
        df = df[df["Diet_type"] == diet]
    if keyword:
        df = df[df["Recipe_name"].str.lower().str.contains(keyword, regex=False)
                | df["Cuisine_type"].str.lower().str.contains(keyword, regex=False)]

    total = int(len(df))
    total_pages = max(1, -(-total // page_size))     # ceiling division
    start = (page - 1) * page_size
    window = df.iloc[start:start + page_size]

    # No matches is an empty page, not an error.
    return _respond({
        "items": window[TEXT_COLS + MACROS].to_dict(orient="records"),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    })
