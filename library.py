import hashlib
import json
import os
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

try:
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi
except ImportError:
    MongoClient = None
    ServerApi = None


DATA_DIR = Path(__file__).resolve().parent / "LoginPage"
DEFAULT_MONGODB_URI = "mongodb+srv://hazengraz426_db_user:BjHNNa6uLBjOwcI1@cluster0.vcr1cig.mongodb.net/?appName=Cluster0"
DEFAULT_DB_NAME = "Library" 
SESSION_DURATION_SECONDS = 60 * 60 * 8

def _log_mongodb_action(action, collection):
    print(f"[MONGODB] {action} on collection '{collection}' in database '{DEFAULT_DB_NAME}'")
BOOKS_CACHE_TTL_SECONDS = 1
SESSION_CACHE_TTL_SECONDS = 1

_mongo_clients = {}
_mongo_clients_lock = Lock()
_books_cache = {}
_books_cache_lock = Lock()
_session_cache = {}
_session_cache_lock = Lock()


def _load_json(filename):
    with open(DATA_DIR / filename, "r", encoding="utf-8") as file:
        return json.load(file)


def _normalize_documents(documents):
    normalized = []

    for document in documents:
        normalized_doc = dict(document)
        normalized_doc.pop("_id", None)
        if "_id" in document and isinstance(document["_id"], str):
            normalized_doc["_id"] = document["_id"]
        normalized.append(normalized_doc)

    return normalized


def _normalize_loan_entries(entries):
    if entries is None:
        return []

    if isinstance(entries, list):
        return entries

    if isinstance(entries, dict):
        if "copy_no" in entries or "loan_id" in entries:
            return [entries]

        try:
            sorted_items = sorted(entries.items(), key=lambda item: int(item[0]))
            return [value for _, value in sorted_items if isinstance(value, dict)]
        except (ValueError, TypeError):
            return [value for value in entries.values() if isinstance(value, dict)]

    return []


def _cache_key(uri, db_name):
    mongo_uri = uri or os.getenv("LIBRARY_MONGODB_URI") or DEFAULT_MONGODB_URI
    return mongo_uri, db_name


def _session_cache_key(session_id, uri=None, db_name=DEFAULT_DB_NAME):
    return (*_cache_key(uri, db_name), session_id)


def _session_document_id(session_id):
    return f"session:{session_id}"


def _active_user_document_id(user_id):
    return f"active_user:{user_id}"


def get_mongodb_database(uri=None, db_name=DEFAULT_DB_NAME):
    if MongoClient is None:
        raise ImportError("pymongo is not installed. Run `pip install pymongo[srv]` first.")

    mongo_uri, cache_db_name = _cache_key(uri, db_name)

    with _mongo_clients_lock:
        client = _mongo_clients.get(mongo_uri)

        if client is None:
            client = MongoClient(mongo_uri, server_api=ServerApi("1"))
            _mongo_clients[mongo_uri] = client

    return client[cache_db_name]


def warm_mongodb_connection(uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    database.command("ping")
    return database


def _read_profile_by_role(database, user_id, role):
    collection_name = "members" if role == "member" else "librarians"
    profile = database[collection_name].find_one({"_id": user_id}) or {}
    normalized = _normalize_documents([profile])[0] if profile else {}
    normalized.setdefault("_id", user_id)
    normalized.setdefault("name", "")
    normalized.setdefault("email", "")
    normalized["current_loans"] = _normalize_loan_entries(normalized.get("current_loans"))
    normalized["loan_history"] = _normalize_loan_entries(normalized.get("loan_history"))
    normalized.setdefault("late_fee", 0)
    return normalized


def _resolve_session_profile(database, user_id, role=None):
    member_profile = database["members"].find_one({"_id": user_id})
    if member_profile:
        return "member", _read_profile_by_role(database, user_id, "member")

    librarian_profile = database["librarians"].find_one({"_id": user_id})
    if librarian_profile:
        return "librarian", _read_profile_by_role(database, user_id, "librarian")

    if role in {"member", "librarian"}:
        return role, _read_profile_by_role(database, user_id, role)

    return role or "member", {
        "_id": user_id,
        "name": "",
        "email": "",
        "current_loans": [],
        "loan_history": [],
        "late_fee": 0
    }


def _build_account_payload(user, profile):
    resolved_profile = profile or {}
    current_loans = _normalize_loan_entries(resolved_profile.get("current_loans"))
    loan_history = _normalize_loan_entries(resolved_profile.get("loan_history"))

    return {
        "_id": resolved_profile.get("_id", user.get("_id") or user.get("user_id")),
        "name": resolved_profile.get("name", user.get("name", "")),
        "email": resolved_profile.get("email", user.get("email", "")),
        "role": user.get("role", "member"),
        "current_loans": current_loans,
        "loan_history": loan_history,
        "late_fee": resolved_profile.get("late_fee", 0),
        "phone": resolved_profile.get("phone")
    }


def _normalize_profile_document(document, fallback_id=None):
    normalized = dict(document or {})
    if fallback_id and "_id" not in normalized:
        normalized["_id"] = fallback_id
    normalized.setdefault("name", "")
    normalized.setdefault("email", "")
    normalized["current_loans"] = _normalize_loan_entries(normalized.get("current_loans"))
    normalized["loan_history"] = _normalize_loan_entries(normalized.get("loan_history"))
    normalized.setdefault("late_fee", 0)
    return normalized


def _default_current_page(role):
    if role == "member":
        return "/LoginPage/dashboard.html"
    return "/LoginPage/dashboard.html"


def _utc_now():
    return datetime.now(timezone.utc)


def _format_session_datetime(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_expiry_datetime():
    return _utc_now() + timedelta(seconds=SESSION_DURATION_SECONDS)


def _parse_session_datetime(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return None


def _session_is_expired(session):
    expires_at = _parse_session_datetime(session.get("expires_at"))
    if not expires_at:
        return True
    return expires_at <= _utc_now()


def _normalize_book_record(book):
    author = book.get("author")
    author_name = author.get("name") if isinstance(author, dict) else author
    copies = []

    for copy in book.get("copies", []):
        if not isinstance(copy, dict):
            continue
        copies.append({
            "copy_no": str(copy.get("copy_no", "")),
            "status": str(copy.get("status", "Unknown"))
        })

    return {
        "_id": str(book.get("_id", "")),
        "title": str(book.get("title", "")),
        "author": {"name": author_name or "Unknown"},
        "year": book.get("year"),
        "copies": copies
    }


def get_account_profile_mongodb(user_id, role="member", uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    _, profile = _resolve_session_profile(database, user_id, role)
    return profile


def get_current_account_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    current_user = get_current_session_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not current_user:
        return None

    profile = get_account_profile_mongodb(current_user["_id"], role=current_user["role"], uri=uri, db_name=db_name)
    return _build_account_payload(current_user, profile)


def get_current_profile_document_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    if not session_id:
        return None

    session = database["login_status"].find_one({"_id": _session_document_id(session_id)})

    if not session or not session.get("is_logged_in") or not session.get("current_user_id"):
        return None

    if _session_is_expired(session):
        database["login_status"].delete_one({"_id": _session_document_id(session_id)})
        return None

    current_user_id = session["current_user_id"]
    member_profile = database["members"].find_one({"_id": current_user_id})
    if member_profile:
        normalized_member = _normalize_profile_document(member_profile, fallback_id=current_user_id)
        normalized_member["role"] = "member"
        return normalized_member

    librarian_profile = database["librarians"].find_one({"_id": current_user_id})
    if librarian_profile:
        normalized_librarian = _normalize_profile_document(librarian_profile, fallback_id=current_user_id)
        normalized_librarian["role"] = "librarian"
        return normalized_librarian

    return None


def get_current_loans_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        return None

    return {
        "user": profile,
        "current_loans": _normalize_loan_entries(profile.get("current_loans")),
        "loan_history": _normalize_loan_entries(profile.get("loan_history"))
    }


def get_library_page_data_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        return None

    books = get_books_mongodb(uri=uri, db_name=db_name)
    return {
        "user": profile,
        "books": books
    }


def _invalidate_runtime_caches(uri=None, db_name=DEFAULT_DB_NAME):
    key = _cache_key(uri, db_name)

    with _books_cache_lock:
        _books_cache.pop(key, None)

    with _session_cache_lock:
        _session_cache.pop(key, None)


def _find_session_document(database, session_id=None):
    if not session_id:
        return None

    session = database["login_status"].find_one({"session_id": session_id})
    if not session:
        return None

    if _session_is_expired(session):
        database["login_status"].delete_one({"_id": session["_id"]})
        return None

    return session


def authenticate_user_mongodb(login_id, password, uri=None, db_name=DEFAULT_DB_NAME):
    _log_mongodb_action("Authenticating user", "accounts")
    database = get_mongodb_database(uri=uri, db_name=db_name)
    account = database["accounts"].find_one({"login_id": login_id, "account_status": "active"})

    if not account:
        return None

    if account.get("password_hash") != double_hash(password):
        return None

    profile = _read_profile_by_role(database, account["user_id"], account.get("role"))

    return {
        "account_id": account.get("account_id"),
        "user_id": account.get("user_id"),
        "login_id": account.get("login_id"),
        "role": account.get("role"),
        "name": profile.get("name", account.get("name")),
        "email": profile.get("email", account.get("email")),
        "profile": profile
    }


def load_data_from_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)

    loaded_accounts = _normalize_documents(database["accounts"].find())
    loaded_books = _normalize_documents(database["books"].find())
    loaded_members = _normalize_documents(database["members"].find())
    loaded_librarians = _normalize_documents(database["librarians"].find())

    return {
        "accounts": loaded_accounts,
        "books": loaded_books,
        "members": loaded_members,
        "librarians": loaded_librarians
    }


def sync_local_json_to_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    """Push all local JSON seed data into MongoDB. Safe to run multiple times."""
    database = get_mongodb_database(uri=uri, db_name=db_name)

    # accounts: delete any old docs with ObjectId _id, then upsert with account_id as _id
    for document in SEED_ACCOUNTS:
        doc_with_id = dict(document)
        doc_with_id["_id"] = document["account_id"]
        # Remove old doc matching account_id (may have an ObjectId _id from before)
        database["accounts"].delete_many({"account_id": document["account_id"], "_id": {"$ne": document["account_id"]}})
        database["accounts"].replace_one({"_id": document["account_id"]}, doc_with_id, upsert=True)

    # members: _id is string like "M001"
    for document in SEED_MEMBERS:
        database["members"].replace_one({"_id": document["_id"]}, document, upsert=True)

    # librarians: _id is string like "L001"
    for document in SEED_LIBRARIANS:
        database["librarians"].replace_one({"_id": document["_id"]}, document, upsert=True)

    # books: _id is the ISBN string
    for document in SEED_BOOKS:
        database["books"].replace_one({"_id": document["_id"]}, document, upsert=True)

    _invalidate_runtime_caches(uri=uri, db_name=db_name)
    print(f"[SYNC] Seeded all collections into '{db_name}' database.")


def create_user_session_mongodb(user, session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    existing_session = database["login_status"].find_one({"current_user_id": user.get("user_id")})
    previous_session_id = existing_session.get("session_id") if existing_session else None
    resolved_session_id = previous_session_id or session_id or secrets.token_urlsafe(32)
    login_time = _utc_now()
    expires_at = _new_expiry_datetime()
    current_page = (
        existing_session.get("current_page")
        if existing_session and existing_session.get("current_page")
        else _default_current_page(user.get("role"))
    )
    payload = {
        "_id": _active_user_document_id(user.get("user_id")),
        "session_id": resolved_session_id,
        "is_logged_in": True,
        "current_account_id": user.get("account_id"),
        "current_user_id": user.get("user_id"),
        "role": user.get("role"),
        "name": user.get("name"),
        "email": user.get("email"),
        "current_page": current_page,
        "login_time": _format_session_datetime(login_time),
        "expires_at": _format_session_datetime(expires_at)
    }
    database["login_status"].replace_one({"_id": _active_user_document_id(user.get("user_id"))}, payload, upsert=True)

    with _session_cache_lock:
        if previous_session_id and previous_session_id != resolved_session_id:
            _session_cache.pop(_session_cache_key(previous_session_id, uri=uri, db_name=db_name), None)
        _session_cache[_session_cache_key(resolved_session_id, uri=uri, db_name=db_name)] = {
            "value": {
                "_id": user.get("user_id"),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": user.get("role"),
                "current_page": current_page,
                "current_loans": _normalize_loan_entries(user.get("profile", {}).get("current_loans")),
                "loan_history": _normalize_loan_entries(user.get("profile", {}).get("loan_history")),
                "late_fee": user.get("profile", {}).get("late_fee", 0),
                "phone": user.get("profile", {}).get("phone")
            } if user else None,
            "expires_at": time.time() + SESSION_CACHE_TTL_SECONDS
        }

    return {
        "session_id": resolved_session_id,
        "current_page": current_page
    }


def clear_user_session_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    if not session_id:
        return

    database = get_mongodb_database(uri=uri, db_name=db_name)
    session = _find_session_document(database, session_id=session_id)
    if session:
        database["login_status"].delete_one({"_id": session["_id"]})

    with _session_cache_lock:
        _session_cache.pop(_session_cache_key(session_id, uri=uri, db_name=db_name), None)


def update_login_status_mongodb(user=None, uri=None, db_name=DEFAULT_DB_NAME):
    if user is None:
        return None
    return create_user_session_mongodb(user, uri=uri, db_name=db_name)


def update_session_current_page_mongodb(session_id, current_page, uri=None, db_name=DEFAULT_DB_NAME):
    if not session_id or not current_page:
        return None

    database = get_mongodb_database(uri=uri, db_name=db_name)
    session = _find_session_document(database, session_id=session_id)
    if not session:
        return None

    database["login_status"].update_one(
        {"_id": session["_id"]},
        {"$set": {
            "current_page": current_page,
            "expires_at": _format_session_datetime(_new_expiry_datetime())
        }}
    )

    with _session_cache_lock:
        cached_session = _session_cache.get(_session_cache_key(session_id, uri=uri, db_name=db_name))
        if cached_session and cached_session.get("value"):
            cached_session["value"]["current_page"] = current_page
            cached_session["expires_at"] = time.time() + SESSION_CACHE_TTL_SECONDS

    return current_page


def get_current_session_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    if not session_id:
        return None

    key = _session_cache_key(session_id, uri=uri, db_name=db_name)

    with _session_cache_lock:
        cached_session = _session_cache.get(key)
        if cached_session and cached_session["expires_at"] > time.time():
            return deepcopy(cached_session["value"])

    # Check database if cache expired
    _log_mongodb_action("Fetching current session", "login_status")
    database = get_mongodb_database(uri=uri, db_name=db_name)
    session = _find_session_document(database, session_id=session_id)

    if not session or not session.get("is_logged_in") or not session.get("current_user_id"):
        return None

    role, profile = _resolve_session_profile(database, session["current_user_id"], session.get("role"))
    current_user = {
        "_id": session.get("current_user_id"),
        "name": profile.get("name", session.get("name")),
        "email": profile.get("email", session.get("email")),
        "role": role,
        "current_page": session.get("current_page", _default_current_page(role)),
        "current_loans": profile["current_loans"],
        "loan_history": profile["loan_history"],
        "late_fee": profile["late_fee"],
        "phone": profile.get("phone")
    }

    with _session_cache_lock:
        _session_cache[key] = {
            "value": deepcopy(current_user),
            "expires_at": time.time() + SESSION_CACHE_TTL_SECONDS
        }

    return current_user


def get_books_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    key = _cache_key(uri, db_name)

    with _books_cache_lock:
        cached_books = _books_cache.get(key)
        if cached_books and cached_books["expires_at"] > time.time():
            return deepcopy(cached_books["value"])

    # Fresh fetch if cache expired
    _log_mongodb_action("Fetching all books", "books")
    database = get_mongodb_database(uri=uri, db_name=db_name)
    books = [_normalize_book_record(book) for book in _normalize_documents(database["books"].find())]

    with _books_cache_lock:
        _books_cache[key] = {
            "value": deepcopy(books),
            "expires_at": time.time() + BOOKS_CACHE_TTL_SECONDS
        }

    return books


def get_members_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    return _normalize_documents(database["members"].find())


def get_next_member_id_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    members = get_members_mongodb(uri=uri, db_name=db_name)
    max_id = 0

    for member in members:
        raw_id = str(member.get("_id", ""))
        try:
            numeric_id = int(raw_id.replace("M", ""))
        except ValueError:
            continue
        max_id = max(max_id, numeric_id)

    return f"M{str(max_id + 1).zfill(3)}"


def register_member_mongodb(name, email, password, uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    normalized_email = email.strip().lower()

    existing_member = database["members"].find_one({"email": {"$regex": f"^{normalized_email}$", "$options": "i"}})
    if existing_member:
        raise ValueError("A member with that email already exists.")

    member_id = get_next_member_id_mongodb(uri=uri, db_name=db_name)
    member_doc = {
        "_id": member_id,
        "name": name.strip(),
        "email": email.strip(),
        "current_loans": [],
        "loan_history": [],
        "late_fee": 0
    }
    account_doc = {
        "account_id": f"A{str(database['accounts'].count_documents({}) + 1).zfill(3)}",
        "user_id": member_id,
        "role": "member",
        "name": name.strip(),
        "email": email.strip(),
        "login_id": member_id,
        "password_hash": double_hash(password),
        "account_status": "active"
    }

    database["members"].insert_one(member_doc)
    database["accounts"].insert_one(account_doc)
    _invalidate_runtime_caches(uri=uri, db_name=db_name)

    return {
        "account_id": account_doc["account_id"],
        "user_id": member_id,
        "login_id": member_id,
        "role": "member",
        "name": member_doc["name"],
        "email": member_doc["email"],
        "profile": member_doc
    }


try:
    SEED_ACCOUNTS = _load_json("accounts.json")
    SEED_BOOKS = _load_json("books.json")
    SEED_MEMBERS = _load_json("members.json")
    SEED_LIBRARIANS = _load_json("librarians.json")
except FileNotFoundError:
    SEED_ACCOUNTS = []
    SEED_BOOKS = []
    SEED_MEMBERS = []
    SEED_LIBRARIANS = []

def _find_account(login_id):
    database = get_mongodb_database()
    return database["accounts"].find_one({"login_id": login_id})

def _find_member(member_id):
    database = get_mongodb_database()
    return database["members"].find_one({"_id": member_id})

def _find_copy(copy_no):
    database = get_mongodb_database()
    book = database["books"].find_one({"copies.copy_no": copy_no})
    if not book:
        return None, None
    copy = next((c for c in book.get("copies", []) if c["copy_no"] == copy_no), None)
    return book, copy




def reset_data_from_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    """Reload seed data lists from MongoDB (for CLI/legacy use only)."""
    load_data_from_mongodb(uri=uri, db_name=db_name)  # validates connection


def double_hash(password):
    first_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hashlib.sha256(first_hash.encode("utf-8")).hexdigest()




def login(login_id, password):
    database = get_mongodb_database()
    account = database["accounts"].find_one({"login_id": login_id, "account_status": "active"})
    if not account:
        return False
    return account.get("password_hash") == double_hash(password)


def issue_book(copy_no, member_id):
    member = _find_member(member_id)
    if not member:
        return "Member does not exist"

    book, copy = _find_copy(copy_no)
    if not copy:
        return "Book copy does not exist"

    if copy["status"] != "Available":
        return "Book not available"

    database = get_mongodb_database()
    copy["status"] = "On Loan"
    
    # Calculate next loan number globally using MongoDB data
    all_members = list(database["members"].find())
    next_loan_number = sum(len(_normalize_loan_entries(m.get("current_loans"))) + len(_normalize_loan_entries(m.get("loan_history"))) for m in all_members) + 1
    
    normalized_current_loans = _normalize_loan_entries(member.get("current_loans"))
    normalized_current_loans.append({
        "copy_no": copy_no,
        "book_title": book["title"],
        "loan_id": f"L{next_loan_number:03d}",
        "loan_date": "2026-04-06",
        "due_date": "2026-04-20"
    })
    member["current_loans"] = normalized_current_loans

    try:
        if MongoClient is not None:
            database = get_mongodb_database()
            database["members"].update_one({"_id": member_id}, {"$set": {"current_loans": normalized_current_loans}})
            database["books"].update_one({"_id": book["_id"], "copies.copy_no": copy_no}, {"$set": {"copies.$.status": "On Loan"}})
            _invalidate_runtime_caches()
    except Exception as e:
        print(f"Failed to sync issue_book with MongoDB: {e}")

    return "Book issued successfully"


def return_book(copy_no, member_id):
    member = _find_member(member_id)
    if not member:
        return "Member does not exist"

    book, copy = _find_copy(copy_no)
    if not copy:
        return "Book copy does not exist"

    normalized_current_loans = _normalize_loan_entries(member.get("current_loans"))
    loan = next((loan_record for loan_record in normalized_current_loans if loan_record["copy_no"] == copy_no), None)
    if not loan:
        return "Book was not issued to this member"

    normalized_current_loans.remove(loan)
    member["current_loans"] = normalized_current_loans
    normalized_history = _normalize_loan_entries(member.get("loan_history"))
    returned_loan = dict(loan)
    returned_loan["return_date"] = "2026-04-06"
    normalized_history.append(returned_loan)
    member["loan_history"] = normalized_history
    copy["status"] = "Available"

    try:
        if MongoClient is not None:
            database = get_mongodb_database()
            database["members"].update_one({"_id": member_id}, {"$set": {
                "current_loans": normalized_current_loans,
                "loan_history": normalized_history
            }})
            database["books"].update_one({"_id": book["_id"], "copies.copy_no": copy_no}, {"$set": {"copies.$.status": "Available"}})
            _invalidate_runtime_caches()
    except Exception as e:
        print(f"Failed to sync return_book with MongoDB: {e}")

    return "Book returned successfully"


def main():
    print("=== Library System ===")

    use_mongodb = input("Load data from MongoDB? (y/n): ").strip().lower()
    if use_mongodb == "y":
        try:
            reset_data_from_mongodb()
            print("MongoDB data loaded successfully.")
        except Exception as error:
            print(f"Could not load MongoDB data: {error}")
            print("Falling back to local JSON files.")
            reset_data()

    login_id = input("Enter login ID: ")
    password = input("Enter password: ")

    if not login(login_id, password):
        print("Login failed!")
        return

    print("Login successful!")

    while True:
        print("\n1. Issue Book")
        print("2. Return Book")
        print("3. Exit")

        choice = input("Choose option: ")

        if choice == "1":
            member_id = input("Enter Member ID: ")
            copy_no = input("Enter Copy Number: ")
            print(issue_book(copy_no, member_id))

        elif choice == "2":
            member_id = input("Enter Member ID: ")
            copy_no = input("Enter Copy Number: ")
            print(return_book(copy_no, member_id))

        elif choice == "3":
            print("Goodbye!")
            break

        else:
            print("Invalid choice!")


# Removed auto-reset of global lists to prioritize MongoDB
# reset_data()


if __name__ == "__main__":
    main()
