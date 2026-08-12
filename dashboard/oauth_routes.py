import os, secrets, urllib.parse, datetime as dt
import requests, jwt
import azure.functions as func
from auth_core import (find_user_by_email, create_user, issue_token,
                       touch_last_login, error, JWT_SECRET, FRONTEND)

bp = func.Blueprint()

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:7071")

PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token":     "https://oauth2.googleapis.com/token",
        "userinfo":  "https://openidconnect.googleapis.com/v1/userinfo",
        "scope":     "openid email profile",
        "client_id":     os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token":     "https://github.com/login/oauth/access_token",
        "userinfo":  "https://api.github.com/user",
        "scope":     "read:user user:email",
        "client_id":     os.environ.get("GITHUB_CLIENT_ID", ""),
        "client_secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
    },
}

def redirect_uri(provider):
    return f"{API_BASE}/api/auth/{provider}/callback"

def make_state(provider):
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode({"p": provider, "n": secrets.token_urlsafe(16),
                       "exp": now + dt.timedelta(minutes=10)},
                      JWT_SECRET, algorithm="HS256")

def check_state(state, provider):
    try:
        return jwt.decode(state, JWT_SECRET, algorithms=["HS256"])["p"] == provider
    except jwt.PyJWTError:
        return False


@bp.route(route="auth/{provider}/start", methods=["GET"],
          auth_level=func.AuthLevel.ANONYMOUS)
def oauth_start(req: func.HttpRequest) -> func.HttpResponse:
    provider = req.route_params.get("provider")
    cfg = PROVIDERS.get(provider)
    if not cfg or not cfg["client_id"]:
        return error("Unsupported or unconfigured provider.", 404)

    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri(provider),
        "response_type": "code",
        "scope":         cfg["scope"],
        "state":         make_state(provider),
    }
    if provider == "google":
        params["prompt"] = "select_account"

    url = cfg["authorize"] + "?" + urllib.parse.urlencode(params)
    return func.HttpResponse(status_code=302, headers={"Location": url})


@bp.route(route="auth/{provider}/callback", methods=["GET"],
          auth_level=func.AuthLevel.ANONYMOUS)
def oauth_callback(req: func.HttpRequest) -> func.HttpResponse:
    provider = req.route_params.get("provider")
    cfg = PROVIDERS.get(provider)
    code  = req.params.get("code")
    state = req.params.get("state")

    if not cfg:
        return error("Unsupported provider.", 404)
    if req.params.get("error"):
        return _bounce(f"{FRONTEND}/login.html?error=access_denied")
    if not code or not check_state(state, provider):
        return _bounce(f"{FRONTEND}/login.html?error=bad_state")

    tok = requests.post(cfg["token"], timeout=10,
        headers={"Accept": "application/json"},
        data={"client_id": cfg["client_id"],
              "client_secret": cfg["client_secret"],
              "code": code,
              "redirect_uri": redirect_uri(provider),
              "grant_type": "authorization_code"}).json()

    access_token = tok.get("access_token")
    if not access_token:
        return _bounce(f"{FRONTEND}/login.html?error=token_exchange_failed")

    api = {"Authorization": f"Bearer {access_token}",
           "Accept": "application/vnd.github+json"}
    profile = requests.get(cfg["userinfo"], headers=api, timeout=10).json()

    if provider == "google":
        email, name, pid = profile.get("email"), profile.get("name"), profile.get("sub")
    else:
        email, name, pid = profile.get("email"), (profile.get("name")
                                                  or profile.get("login")), str(profile.get("id"))
        if not email:
            emails = requests.get("https://api.github.com/user/emails",
                                  headers=api, timeout=10).json()
            primary = next((e for e in emails
                            if e.get("primary") and e.get("verified")), None)
            email = primary["email"] if primary else None

    if not email:
        return _bounce(f"{FRONTEND}/login.html?error=no_email")

    user = find_user_by_email(email)
    if not user:
        user = create_user(email, name, password=None,
                           provider=provider, provider_id=pid)
    else:
        touch_last_login(user)

    return _bounce(f"{FRONTEND}/login.html#token={issue_token(user)}")


def _bounce(url):
    return func.HttpResponse(status_code=302, headers={"Location": url})