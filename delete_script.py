import os
import ssl
import requests
from requests.adapters import HTTPAdapter

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        # Allow legacy renegotiation (needed for older IIS/OpenSSL servers)
        ctx.options |= 0x4  # ssl.OP_LEGACY_SERVER_CONNECT
        ctx.check_hostname = True
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def get_session():
    session = requests.Session()
    session.mount("https://", LegacySSLAdapter())
    return session


# ---------------------------------------------------------------------------
# CONFIG — edit these before running (or override via env vars in CI)
# ---------------------------------------------------------------------------

LAYER_URL = os.environ.get(
    "LAYER_URL",
    "https://amangis.in/gisserver/rest/services/WUA/solar_scheme_dashboard_service_new/FeatureServer/0",
)

# Object IDs of the records you want to delete, e.g. [1, 2, 5]
OBJECT_IDS_TO_DELETE = []

# Alternative: delete using a WHERE clause instead of explicit OIDs.
DELETE_WHERE = os.environ.get("DELETE_WHERE")  # e.g. "STATUS = 'INACTIVE'"

# Set via env var CONFIRM_DELETE_ALL=true to delete every record in the layer.
DELETE_ALL = os.environ.get("DELETE_ALL", "false").lower() == "true"

# Token from GitHub Secrets (never hardcode this)
TOKEN = os.environ.get("ARCGIS_TOKEN")

# In CI there's no interactive prompt, so we require an explicit confirmation
# phrase passed in as an env var / workflow input instead of input().
CONFIRM_PHRASE = os.environ.get("CONFIRM_PHRASE", "")


# ---------------------------------------------------------------------------
# Helper: resolve object IDs from a WHERE clause, if provided
# ---------------------------------------------------------------------------

def get_object_ids_by_where(layer_url, where_clause, token=None):
    query_url = f"{layer_url}/query"
    params = {
        "where": where_clause,
        "returnIdsOnly": "true",
        "f": "json",
    }
    if token:
        params["token"] = token

    session = get_session()
    resp = session.get(query_url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"Query failed: {data['error']}")

    return data.get("objectIds", [])


# ---------------------------------------------------------------------------
# Core delete function
# ---------------------------------------------------------------------------

def delete_records(layer_url, object_ids, token=None, batch_size=500):
    if not object_ids:
        print("No object IDs to delete. Exiting.")
        return

    edit_url = f"{layer_url}/applyEdits"
    total_success = 0
    total_fail = 0
    session = get_session()

    for i in range(0, len(object_ids), batch_size):
        batch = object_ids[i:i + batch_size]

        payload = {
            "deletes": ",".join(str(oid) for oid in batch),
            "f": "json",
        }
        if token:
            payload["token"] = token

        resp = session.post(edit_url, data=payload)
        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            raise RuntimeError(f"applyEdits failed: {result['error']}")

        delete_results = result.get("deleteResults", [])
        batch_success = sum(1 for r in delete_results if r.get("success"))
        batch_fail = len(delete_results) - batch_success

        total_success += batch_success
        total_fail += batch_fail

        print(f"Batch {i // batch_size + 1}: {batch_success} succeeded, {batch_fail} failed.")

        for r in delete_results:
            if not r.get("success"):
                print(f"  Failed OID {r.get('objectId')}: {r.get('error')}")

    print(f"\nTotal: {total_success} succeeded, {total_fail} failed out of {len(object_ids)} records.")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    oids = OBJECT_IDS_TO_DELETE

    if DELETE_ALL:
        print("DELETE_ALL is set — fetching all object IDs in the layer...")
        oids = get_object_ids_by_where(LAYER_URL, "1=1", token=TOKEN)
    elif not oids and DELETE_WHERE:
        print(f"Resolving object IDs for WHERE clause: {DELETE_WHERE}")
        oids = get_object_ids_by_where(LAYER_URL, DELETE_WHERE, token=TOKEN)

    if not oids:
        print("No records found/specified for deletion.")
    else:
        print(f"{len(oids)} record(s) will be PERMANENTLY deleted from:\n  {LAYER_URL}")
        # Non-interactive confirmation check (CI has no stdin prompt)
        if CONFIRM_PHRASE.strip() == "DELETE":
            delete_records(LAYER_URL, oids, token=TOKEN)
        else:
            print("Aborted. CONFIRM_PHRASE env var was not set to 'DELETE'. No records were deleted.")
