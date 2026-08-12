# Phase 3 API Contract

The dashboard UI (`dashboard/index.html`) is already written against this contract.
Backend work should implement these endpoints exactly — no UI changes needed once they are live.

**Base URL** is set in one place, at the top of `index.html`:

```js
const CONFIG = {
  API_BASE: "https://diet-insights-func-karan.azurewebsites.net/api",
  ...
};
```

Every endpoint below must send `Access-Control-Allow-Origin: *` (or the Static Web App origin),
and must allow the `Authorization` and `Content-Type` headers on preflight (`OPTIONS`).

---

## 1. Auth

Protected endpoints expect `Authorization: Bearer <token>`.

### `POST /auth/register`

```jsonc
// request
{ "name": "Karan Pabla", "email": "karan@example.com", "password": "plaintext-over-https" }

// 201 response
{ "token": "<jwt>", "user": { "name": "Karan Pabla", "email": "karan@example.com" } }

// 409 if the email already exists
{ "error": "Email already registered" }
```

Password must be hashed (bcrypt / argon2 / PBKDF2 — never plaintext, never a bare SHA) before it
touches the database. Rubric item: *"Database is encrypted and user's password hash is stored in DB"*.

### `POST /auth/login`

```jsonc
// request
{ "email": "karan@example.com", "password": "..." }

// 200 response — same shape as register
{ "token": "<jwt>", "user": { "name": "Karan Pabla", "email": "karan@example.com" } }

// 401
{ "error": "Invalid email or password" }
```

### `GET /auth/me`

Validates the bearer token on page reload so the session survives refresh.

```jsonc
// 200
{ "user": { "name": "Karan Pabla", "email": "karan@example.com" } }
// 401 -> UI drops back to the login screen
```

### `GET /auth/oauth/{provider}/start?redirect_uri=<url>`

`{provider}` is `google` or `github`. This is a **browser redirect**, not a fetch.

The UI sends the user to this URL. After the provider consents, the backend must redirect back to
`redirect_uri` with the result in the **URL fragment** (fragment, not query string — it must not end
up in server logs or the Referer header):

```
https://<static-web-app>/#token=<jwt>&name=Karan%20Pabla&email=karan%40example.com
```

The UI reads the fragment, stores the session, and immediately strips it from the address bar.
On failure redirect back with `#error=<message>`.

---

## 2. Cached dashboard results — `GET /GetDashboardResults`

This is the endpoint that proves the caching rubric item. It must **not** recompute anything: it
reads the precomputed `dashboard_results.json` blob that the blob-trigger function wrote when
`All_Diets.csv` last changed, and returns it as-is.

```jsonc
{
  "meta": {
    "cache_hit": true,              // false only on the request that rebuilt the cache
    "computed_at": "2026-08-09T04:12:07Z",  // when the blob trigger last recomputed
    "served_at":   "2026-08-11T18:44:01Z",  // now
    "execution_time_ms": 12.4,      // should be tiny vs Phase 2's full recompute
    "source_version": "etag-or-hash-of-All_Diets.csv",
    "row_count": 7806
  },

  // Chart 1 — grouped bar
  "avg_macros_per_diet": [
    { "Diet_type": "dash", "Protein(g)": 69.28, "Carbs(g)": 160.54, "Fat(g)": 101.15 }
  ],

  // Chart 2 — scatter. Sample/downsample to ~300 points; the browser does not need 7806.
  "protein_vs_carbs": [
    { "Diet_type": "keto", "Recipe_name": "...", "Protein(g)": 101.2, "Carbs(g)": 57.9 }
  ],

  // Chart 3 — heatmap. Square correlation matrix over the macro columns.
  "macro_correlation": {
    "labels": ["Protein(g)", "Carbs(g)", "Fat(g)"],
    "matrix": [[1.0, 0.156, 0.478], [0.156, 1.0, 0.269], [0.478, 0.269, 1.0]]
  },

  // Chart 4 — pie
  "recipe_count_per_diet": [
    { "Diet_type": "dash", "count": 1745 }
  ],

  // Populates the diet dropdown so the UI never hardcodes diet names
  "diet_types": ["dash", "keto", "mediterranean", "paleo", "vegan"]
}
```

`meta.cache_hit` and `meta.computed_at` are rendered in the UI's cache banner. For the video demo:
upload v2 of `All_Diets.csv`, and `computed_at` should change exactly once while `cache_hit` stays
`true` for every subsequent request.

---

## 3. Recipe search — `GET /GetRecipes`

Serves from the cleaned `cleaned_diets.csv` blob (or Cosmos), never from the raw CSV.

| Query param | Type | Default | Notes |
|---|---|---|---|
| `q` | string | `""` | Keyword. Case-insensitive substring over `Recipe_name` **and** `Cuisine_type`. |
| `diet_type` | string | `""` | Exact match, lowercase. Empty = all diets. |
| `page` | int | `1` | 1-based. |
| `page_size` | int | `10` | Cap at 100 server-side. |

```jsonc
{
  "items": [
    {
      "Diet_type": "keto",
      "Recipe_name": "Keto Sausage Balls",
      "Cuisine_type": "american",
      "Protein(g)": 211.1,
      "Carbs(g)": 620.2,
      "Fat(g)": 208.5
    }
  ],
  "page": 1,
  "page_size": 10,
  "total": 137,        // total matches BEFORE pagination — required to render page numbers
  "total_pages": 14
}
```

`total` is what drives the Prev / 1 2 3 / Next control. If it is missing or wrong, pagination breaks.

An empty result set is `200` with `"items": []` and `"total": 0` — **not** a 404.

---

## Error shape

Every non-2xx response uses the same body so the UI can surface it:

```jsonc
{ "error": "human readable message" }
```
