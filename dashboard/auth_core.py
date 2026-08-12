import os, json, uuid, datetime as dt
import bcrypt, jwt
import azure.functions as func
from azure.cosmos import CosmosClient, exceptions

_client = CosmosClient(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_KEY"])
_users = (_client
          .get_database_client(os.environ["COSMOS_DB"])
          .get_container_client(os.environ["COSMOS_USERS_CONTAINER"]))

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ISSUER = os.environ.get("JWT_ISSUER", "diet-dashboard")
JWT_TTL    = int(os.environ.get("JWT_TTL_MINUTES", "60"))
FRONTEND   = os.environ.get("FRONTEND_URL", "http://localhost:5500")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()

def verify_password(plain: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    return bcrypt.checkpw(plain.encode(), stored_hash.encode())


def issue_token(user: dict) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub":   user["id"],
        "email": user["email"],
        "name":  user["name"],
        "prov":  user["provider"],
        "iss":   JWT_ISSUER,
        "iat":   now,
        "exp":   now + dt.timedelta(minutes=JWT_TTL),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer=JWT_ISSUER)
    except jwt.PyJWTError:
        return None


def find_user_by_email(email: str):
    email = email.strip().lower()
    rows = list(_users.query_items(
        query="SELECT * FROM c WHERE c.email = @e",
        parameters=[{"name": "@e", "value": email}],
        partition_key=email,
    ))
    return rows[0] if rows else None

def create_user(email, name, password=None, provider="local", provider_id=None) -> dict:
    email = email.strip().lower()
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": name or email.split("@")[0],
        "provider": provider,
        "provider_id": provider_id,
        "password_hash": hash_password(password) if password else None,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_login": None,
    }
    _users.create_item(doc)
    return doc

def touch_last_login(user: dict) -> None:
    user["last_login"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _users.replace_item(item=user["id"], body=user)

def public_view(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"],
            "name": user["name"], "provider": user["provider"]}


def json_response(body: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status,
                             mimetype="application/json")

def error(msg: str, status: int = 400) -> func.HttpResponse:
    return json_response({"error": msg}, status)

def current_user(req: func.HttpRequest):
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return decode_token(header[7:])

def require_auth(handler):
    def wrapper(req: func.HttpRequest) -> func.HttpResponse:
        claims = current_user(req)
        if not claims:
            return error("Unauthorized", 401)
        return handler(req, claims)
    wrapper.__name__ = handler.__name__
    return wrapper