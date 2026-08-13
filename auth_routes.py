import re, json
import azure.functions as func
from azure.cosmos import exceptions
from auth_core import (find_user_by_email, create_user, verify_password,
                       issue_token, touch_last_login, public_view,
                       json_response, error, current_user)

bp = func.Blueprint()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

def validate(email, password):
    if not email or not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    if not password or len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    return None


@bp.route(route="auth/register", methods=["POST"],
          auth_level=func.AuthLevel.ANONYMOUS)
def register(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return error("Invalid JSON body.")

    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    name     = (body.get("name") or "").strip()

    problem = validate(email, password)
    if problem:
        return error(problem)

    if find_user_by_email(email):
        return error("An account with this email already exists.", 409)

    try:
        user = create_user(email, name, password=password, provider="local")
    except exceptions.CosmosResourceExistsError:
        return error("An account with this email already exists.", 409)

    return json_response({"token": issue_token(user),
                          "user": public_view(user)}, 201)


@bp.route(route="auth/login", methods=["POST"],
          auth_level=func.AuthLevel.ANONYMOUS)
def login(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return error("Invalid JSON body.")

    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    user = find_user_by_email(email)

    if not user or not verify_password(password, user.get("password_hash")):
        return error("Invalid email or password.", 401)

    touch_last_login(user)
    return json_response({"token": issue_token(user),
                          "user": public_view(user)})


@bp.route(route="auth/me", methods=["GET"],
          auth_level=func.AuthLevel.ANONYMOUS)
def me(req: func.HttpRequest) -> func.HttpResponse:
    claims = current_user(req)
    if not claims:
        return error("Unauthorized", 401)
    return json_response({"user": {"id": claims["sub"], "email": claims["email"],
                                   "name": claims["name"], "provider": claims["prov"]}})
