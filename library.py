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
SESSION_TIMEOUT_MINUTES = 10
SESSION_HEARTBEAT_SECONDS = SESSION_TIMEOUT_MINUTES * 60
DAILY_FINE_RATE = 2.0

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
_indexes_ready = set()
_indexes_ready_lock = Lock()


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


def _normalize_payment_documents(documents):
    normalized_payments = []

    for document in documents or []:
        normalized_payment = dict(document or {})
        normalized_payment["_id"] = str(normalized_payment.get("_id", "")).strip() or None
        normalized_payment["member_id"] = str(normalized_payment.get("member_id", "")).strip()
        normalized_payment["member_name"] = str(normalized_payment.get("member_name", "")).strip()
        normalized_payment["payment_method"] = str(normalized_payment.get("payment_method", "")).strip() or "counter_cash"
        normalized_payment["processed_by"] = str(normalized_payment.get("processed_by", "")).strip()
        normalized_payment["processed_at"] = normalized_payment.get("processed_at")
        normalized_payment["receipt_no"] = str(normalized_payment.get("receipt_no", "")).strip()
        normalized_payment["notes"] = str(normalized_payment.get("notes", "")).strip()
        try:
            normalized_payment["amount_paid"] = round(float(normalized_payment.get("amount_paid", 0) or 0), 2)
        except (TypeError, ValueError):
            normalized_payment["amount_paid"] = 0.0
        normalized_payments.append(normalized_payment)

    normalized_payments.sort(
        key=lambda payment: (
            str(payment.get("processed_at") or ""),
            str(payment.get("_id") or "")
        ),
        reverse=True
    )
    return normalized_payments


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

    available_database_names = []
    try:
        available_database_names = client.list_database_names()
    except Exception:
        available_database_names = []

    resolved_db_name = cache_db_name
    normalized_requested_name = str(cache_db_name or "").strip().lower()
    if available_database_names and normalized_requested_name:
        exact_match = next((name for name in available_database_names if name == cache_db_name), None)
        case_insensitive_match = next(
            (name for name in available_database_names if str(name).strip().lower() == normalized_requested_name),
            None
        )
        resolved_db_name = exact_match or case_insensitive_match or cache_db_name

    database = client[resolved_db_name]
    _ensure_login_status_indexes(database, uri=uri, db_name=resolved_db_name)
    return database


def warm_mongodb_connection(uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    database.command("ping")
    return database


def _ensure_login_status_indexes(database, uri=None, db_name=DEFAULT_DB_NAME):
    index_key = _cache_key(uri, db_name)
    with _indexes_ready_lock:
        if index_key in _indexes_ready:
            return

        database["login_status"].create_index("session_id", sparse=True)
        database["login_status"].create_index("current_user_id", sparse=True)
        database["login_status"].create_index("expires_at_ttl", expireAfterSeconds=0)
        _indexes_ready.add(index_key)


def _get_member_fee_payments(database, member_id):
    normalized_member_id = str(member_id or "").strip()
    if not normalized_member_id:
        return []

    payments = database["fee_payments"].find({"member_id": normalized_member_id})
    return _normalize_payment_documents(_normalize_documents(payments))


def _read_profile_by_role(database, user_id, role):
    collection_name = "members" if role == "member" else "librarians"
    profile = database[collection_name].find_one({"_id": user_id}) or {}
    normalized = _normalize_documents([profile])[0] if profile else {}
    normalized.setdefault("_id", user_id)
    normalized.setdefault("name", "")
    normalized.setdefault("email", "")
    normalized["current_loans"] = _normalize_loan_entries(normalized.get("current_loans"))
    normalized["loan_history"] = _normalize_loan_entries(normalized.get("loan_history"))
    normalized["payment_history"] = _get_member_fee_payments(database, user_id) if role == "member" else []
    normalized.setdefault("late_fee", 0)
    recalculated_profile = _apply_member_fine_metrics(normalized)

    if role == "member" and profile:
        recalculated_late_fee = recalculated_profile.get("late_fee", 0)
        stored_late_fee = round(float(profile.get("late_fee", 0) or 0), 2)
        if round(float(recalculated_late_fee or 0), 2) != stored_late_fee:
            database["members"].update_one(
                {"_id": user_id},
                {"$set": {"late_fee": recalculated_late_fee}}
            )

    return recalculated_profile


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
    payment_history = _normalize_payment_documents(resolved_profile.get("payment_history"))

    return {
        "_id": resolved_profile.get("_id", user.get("_id") or user.get("user_id")),
        "name": resolved_profile.get("name", user.get("name", "")),
        "email": resolved_profile.get("email", user.get("email", "")),
        "role": user.get("role", "member"),
        "current_loans": current_loans,
        "loan_history": loan_history,
        "payment_history": payment_history,
        "late_fee": resolved_profile.get("late_fee", 0),
        "outstanding_balance": resolved_profile.get("outstanding_balance", resolved_profile.get("late_fee", 0)),
        "total_overdue_charges": resolved_profile.get("total_overdue_charges", 0),
        "total_payments": resolved_profile.get("total_payments", 0),
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
    normalized["payment_history"] = _normalize_payment_documents(normalized.get("payment_history"))
    normalized.setdefault("late_fee", 0)
    return _apply_member_fine_metrics(normalized)


def _default_current_page(role):
    if role == "member":
        return "/LoginPage/dashboard.html"
    return "/LoginPage/dashboard.html"


def _utc_now():
    return datetime.now(timezone.utc)


def _format_session_datetime(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_expiry_datetime():
    return _utc_now() + timedelta(seconds=SESSION_HEARTBEAT_SECONDS)


def _format_loan_day(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _parse_session_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return None


def _parse_loan_date(value):
    parsed_datetime = _parse_session_datetime(value)
    if parsed_datetime is not None:
        return parsed_datetime.date()

    if isinstance(value, str):
        normalized_value = value.strip()
        if not normalized_value:
            return None
        try:
            return datetime.strptime(normalized_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    return None


def _calculate_days_late(due_date, comparison_date=None):
    parsed_due_date = _parse_loan_date(due_date)
    if parsed_due_date is None:
        return 0

    parsed_comparison_date = _parse_loan_date(comparison_date)
    if parsed_comparison_date is None:
        parsed_comparison_date = _utc_now().date()

    if parsed_comparison_date <= parsed_due_date:
        return 0

    return (parsed_comparison_date - parsed_due_date).days


def _calculate_fine_amount(days_late):
    normalized_days_late = max(0, int(days_late or 0))
    return round(normalized_days_late * DAILY_FINE_RATE, 2)


def _annotate_loan_record(loan, is_history=False):
    normalized_loan = dict(loan or {})
    comparison_date = normalized_loan.get("return_date") if is_history else None
    overdue_days = _calculate_days_late(normalized_loan.get("due_date"), comparison_date)

    normalized_loan["overdue_days"] = overdue_days
    normalized_loan["fine_amount"] = _calculate_fine_amount(overdue_days)
    normalized_loan["is_overdue"] = overdue_days > 0
    return normalized_loan


def _apply_member_fine_metrics(profile):
    normalized_profile = dict(profile or {})
    current_loans = [
        _annotate_loan_record(loan, is_history=False)
        for loan in _normalize_loan_entries(normalized_profile.get("current_loans"))
    ]
    loan_history = [
        _annotate_loan_record(loan, is_history=True)
        for loan in _normalize_loan_entries(normalized_profile.get("loan_history"))
    ]

    payment_history = _normalize_payment_documents(normalized_profile.get("payment_history"))
    total_overdue_charges = round(
        sum(loan.get("fine_amount", 0) for loan in current_loans) +
        sum(loan.get("fine_amount", 0) for loan in loan_history),
        2
    )
    total_payments = round(sum(payment.get("amount_paid", 0) for payment in payment_history), 2)
    try:
        stored_late_fee = round(float(normalized_profile.get("late_fee", 0) or 0), 2)
    except (TypeError, ValueError):
        stored_late_fee = 0.0
    assessed_fee_balance = round(max(total_overdue_charges, stored_late_fee + total_payments), 2)
    outstanding_balance = round(max(0, assessed_fee_balance - total_payments), 2)
    remaining_payments = total_payments

    for loan in current_loans + loan_history:
        fine_amount = round(float(loan.get("fine_amount", 0) or 0), 2)
        applied_payment = round(min(remaining_payments, fine_amount), 2)
        loan["paid_toward_fine"] = applied_payment
        loan["outstanding_fine_amount"] = round(max(0, fine_amount - applied_payment), 2)
        remaining_payments = round(max(0, remaining_payments - applied_payment), 2)

    normalized_profile["current_loans"] = current_loans
    normalized_profile["loan_history"] = loan_history
    normalized_profile["payment_history"] = payment_history
    normalized_profile["assessed_fee_balance"] = assessed_fee_balance
    normalized_profile["unallocated_outstanding_balance"] = round(max(0, outstanding_balance - sum(loan.get("outstanding_fine_amount", 0) for loan in current_loans + loan_history)), 2)
    normalized_profile["total_overdue_charges"] = total_overdue_charges
    normalized_profile["total_payments"] = total_payments
    normalized_profile["late_fee"] = outstanding_balance
    normalized_profile["outstanding_balance"] = outstanding_balance
    return normalized_profile


def _session_is_expired(session):
    expires_at = _parse_session_datetime(session.get("expires_at")) or session.get("expires_at_ttl")
    if not expires_at:
        return True
    return expires_at <= _utc_now()


def _normalize_book_record(book):
    author = book.get("author")
    author_name = author.get("name") if isinstance(author, dict) else author
    raw_copies = book.get("copies")
    if raw_copies is None:
        raw_copies = book.get("Copies", [])
    copies = []

    for copy in raw_copies:
        if not isinstance(copy, dict):
            continue
        copies.append({
            "copy_no": str(copy.get("copy_no", copy.get("Copy_no", ""))),
            "status": str(copy.get("status", copy.get("availability", copy.get("Availability", "Unknown"))))
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

    session = _find_session_document(database, session_id=session_id)

    if not session or not session.get("is_logged_in") or not session.get("current_user_id"):
        return None

    current_user_id = session["current_user_id"]
    member_profile = database["members"].find_one({"_id": current_user_id})
    if member_profile:
        normalized_member = _read_profile_by_role(database, current_user_id, "member")
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


def get_librarian_loans_overview_mongodb(session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    loan_requests = []

    for request in _normalize_documents(database["loan_requests"].find()):
        request_status = str(request.get("status", "")).strip().lower()
        copy_no = str(request.get("copy_no", "")).strip()
        book_id = str(request.get("book_id", "")).strip()
        normalized_request = {
            "_id": request.get("_id"),
            "member_id": request.get("member_id"),
            "member_name": request.get("member_name", "Unknown Member"),
            "member_email": request.get("member_email"),
            "book_id": book_id,
            "book_title": request.get("book_title"),
            "copy_no": copy_no,
            "status": request_status,
            "request_id": request.get("_id"),
            "request_date": request.get("request_date")
        }
        loan_requests.append(normalized_request)

    loan_requests.sort(
        key=lambda request: (
            str(request.get("request_date") or ""),
            str(request.get("_id") or "")
        ),
        reverse=True
    )

    pending_requests = [
        request for request in loan_requests
        if str(request.get("status", "")).strip().lower() == "pending"
    ]

    return {
        "user": profile,
        "loan_requests": loan_requests,
        "pending_requests": pending_requests
    }


def create_loan_request_mongodb(session_id, book_id, copy_no, uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "member":
        raise ValueError("Only members can request loans.")

    if round(float(profile.get("late_fee", 0) or 0), 2) > 0:
        raise ValueError("Members with unpaid late fees cannot request another book.")

    database = get_mongodb_database(uri=uri, db_name=db_name)
    normalized_current_loans = _normalize_loan_entries(profile.get("current_loans"))
    if any(loan.get("copy_no") == copy_no for loan in normalized_current_loans):
        raise ValueError("You already have this copy loaned.")

    existing_request = database["loan_requests"].find_one({
        "member_id": profile.get("_id"),
        "copy_no": copy_no,
        "status": "pending"
    })
    if existing_request:
        raise ValueError("A pending loan request already exists for this copy.")

    normalized_book = None
    for book in get_books_mongodb(uri=uri, db_name=db_name):
        if str(book.get("_id")) != str(book_id):
            continue

        copies = book.get("copies") or []
        if any(str(copy.get("copy_no")) == str(copy_no) for copy in copies):
            normalized_book = book
            break

    if not normalized_book:
        raise ValueError("Book copy does not exist.")

    request_id = f"R{str(database['loan_requests'].count_documents({}) + 1).zfill(3)}"
    request_date = _format_session_datetime(_utc_now())
    request_doc = {
        "_id": request_id,
        "member_id": profile.get("_id"),
        "member_name": profile.get("name"),
        "member_email": profile.get("email"),
        "book_id": book_id,
        "book_title": normalized_book.get("title"),
        "copy_no": copy_no,
        "status": "pending",
        "request_date": request_date
    }
    database["loan_requests"].insert_one(request_doc)
    _invalidate_runtime_caches(uri=uri, db_name=db_name)
    return request_doc


def issue_loan_request_mongodb(session_id, request_id, uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "librarian":
        raise ValueError("Only librarians can issue requested books.")

    database = get_mongodb_database(uri=uri, db_name=db_name)
    request_doc = database["loan_requests"].find_one({"_id": request_id, "status": "pending"})
    if not request_doc:
        raise ValueError("Pending loan request not found.")

    member_id = request_doc.get("member_id")
    copy_no = request_doc.get("copy_no")
    if not member_id or not copy_no:
        raise ValueError("Loan request is missing member or copy information.")

    result = issue_book(copy_no, member_id)
    if result != "Book issued successfully":
        raise ValueError(result)

    database["loan_requests"].update_one(
        {"_id": request_id},
        {"$set": {
            "status": "issued",
            "issued_by": profile.get("_id"),
            "issued_at": _format_session_datetime(_utc_now())
        }}
    )

    _invalidate_runtime_caches(uri=uri, db_name=db_name)
    return {
        "request_id": request_id,
        "member_id": member_id,
        "copy_no": copy_no,
        "message": result
    }


def _invalidate_runtime_caches(uri=None, db_name=DEFAULT_DB_NAME):
    key = _cache_key(uri, db_name)

    with _books_cache_lock:
        _books_cache.pop(key, None)

    with _session_cache_lock:
        session_keys_to_remove = [
            session_key for session_key in _session_cache
            if session_key[:2] == key
        ]
        for session_key in session_keys_to_remove:
            _session_cache.pop(session_key, None)


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
        "last_seen_at": _format_session_datetime(login_time),
        "expires_at": _format_session_datetime(expires_at),
        "expires_at_ttl": expires_at,
        "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
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
                "payment_history": _normalize_payment_documents(user.get("profile", {}).get("payment_history")),
                "late_fee": user.get("profile", {}).get("late_fee", 0),
                "outstanding_balance": user.get("profile", {}).get("outstanding_balance", user.get("profile", {}).get("late_fee", 0)),
                "total_overdue_charges": user.get("profile", {}).get("total_overdue_charges", 0),
                "total_payments": user.get("profile", {}).get("total_payments", 0),
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
        return 0

    database = get_mongodb_database(uri=uri, db_name=db_name)
    session_document = database["login_status"].find_one({"session_id": session_id}) or {}
    delete_filters = [{"session_id": session_id}]
    current_user_id = str(session_document.get("current_user_id", "")).strip()
    document_id = str(session_document.get("_id", "")).strip()
    deleted = 0

    if current_user_id:
        deleted += database["login_status"].delete_one({"_id": _active_user_document_id(current_user_id)}).deleted_count
        delete_filters.append({"current_user_id": current_user_id})
        delete_filters.append({"_id": _active_user_document_id(current_user_id)})
    if document_id:
        deleted += database["login_status"].delete_one({"_id": document_id}).deleted_count
        delete_filters.append({"_id": document_id})

    deleted += database["login_status"].delete_many({"$or": delete_filters}).deleted_count

    with _session_cache_lock:
        _session_cache.pop(_session_cache_key(session_id, uri=uri, db_name=db_name), None)

    return deleted


def clear_all_user_sessions_mongodb(uri=None, db_name=DEFAULT_DB_NAME):
    database = get_mongodb_database(uri=uri, db_name=db_name)
    database["login_status"].delete_many({})

    cache_key = _cache_key(uri, db_name)

    with _session_cache_lock:
        session_keys_to_remove = [
            key for key in _session_cache
            if key[:2] == cache_key
        ]
        for key in session_keys_to_remove:
            _session_cache.pop(key, None)


def clear_user_sessions_for_user_mongodb(user_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return 0

    database = get_mongodb_database(uri=uri, db_name=db_name)
    deleted = database["login_status"].delete_one({
        "_id": _active_user_document_id(normalized_user_id)
    }).deleted_count
    deleted += database["login_status"].delete_many({
        "$or": [
            {"current_user_id": normalized_user_id},
            {"_id": _active_user_document_id(normalized_user_id)},
            {"session_id": normalized_user_id}
        ]
    }).deleted_count

    cache_key = _cache_key(uri, db_name)

    with _session_cache_lock:
        session_keys_to_remove = [
            key for key, value in _session_cache.items()
            if key[:2] == cache_key and value.get("value", {}).get("_id") == normalized_user_id
        ]
        for key in session_keys_to_remove:
            _session_cache.pop(key, None)

    return deleted


def has_active_user_session_mongodb(user_id=None, session_id=None, uri=None, db_name=DEFAULT_DB_NAME):
    normalized_user_id = str(user_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    if not normalized_user_id and not normalized_session_id:
        return False

    database = get_mongodb_database(uri=uri, db_name=db_name)
    query_filters = []
    if normalized_user_id:
        query_filters.append({"_id": _active_user_document_id(normalized_user_id)})
        query_filters.append({"current_user_id": normalized_user_id})
    if normalized_session_id:
        query_filters.append({"session_id": normalized_session_id})

    return database["login_status"].find_one({"$or": query_filters}) is not None


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
            "last_seen_at": _format_session_datetime(_utc_now()),
            "expires_at": _format_session_datetime(_new_expiry_datetime()),
            "expires_at_ttl": _new_expiry_datetime(),
            "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
        }}
    )

    with _session_cache_lock:
        cached_session = _session_cache.get(_session_cache_key(session_id, uri=uri, db_name=db_name))
        if cached_session and cached_session.get("value"):
            cached_session["value"]["current_page"] = current_page
            cached_session["expires_at"] = time.time() + SESSION_CACHE_TTL_SECONDS

    return current_page


def update_user_current_page_mongodb(user_id, current_page, uri=None, db_name=DEFAULT_DB_NAME):
    normalized_user_id = str(user_id or "").strip()
    normalized_current_page = str(current_page or "").strip()
    if not normalized_user_id or not normalized_current_page:
        return None

    database = get_mongodb_database(uri=uri, db_name=db_name)
    database["login_status"].update_one(
        {
            "$or": [
                {"_id": _active_user_document_id(normalized_user_id)},
                {"current_user_id": normalized_user_id}
            ]
        },
        {"$set": {
            "current_page": normalized_current_page,
            "last_seen_at": _format_session_datetime(_utc_now()),
            "expires_at": _format_session_datetime(_new_expiry_datetime()),
            "expires_at_ttl": _new_expiry_datetime(),
            "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
        }}
    )

    cache_key = _cache_key(uri, db_name)
    with _session_cache_lock:
        for key, cached_session in _session_cache.items():
            if key[:2] != cache_key or not cached_session.get("value"):
                continue
            if str(cached_session["value"].get("_id", "")).strip() == normalized_user_id:
                cached_session["value"]["current_page"] = normalized_current_page
                cached_session["expires_at"] = time.time() + SESSION_CACHE_TTL_SECONDS

    return normalized_current_page


def heartbeat_user_session_mongodb(session_id, current_page=None, uri=None, db_name=DEFAULT_DB_NAME):
    if not session_id:
        return None

    database = get_mongodb_database(uri=uri, db_name=db_name)
    session = _find_session_document(database, session_id=session_id)
    if not session:
        return None

    now = _utc_now()
    new_expiry = _new_expiry_datetime()
    updates = {
        "last_seen_at": _format_session_datetime(now),
        "expires_at": _format_session_datetime(new_expiry),
        "expires_at_ttl": new_expiry,
        "session_timeout_minutes": SESSION_TIMEOUT_MINUTES
    }

    if current_page:
        updates["current_page"] = current_page

    database["login_status"].update_one({"_id": session["_id"]}, {"$set": updates})

    with _session_cache_lock:
        cached_session = _session_cache.get(_session_cache_key(session_id, uri=uri, db_name=db_name))
        if cached_session and cached_session.get("value"):
            if current_page:
                cached_session["value"]["current_page"] = current_page
            cached_session["expires_at"] = time.time() + SESSION_CACHE_TTL_SECONDS

    session.update(updates)
    return session


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
        "payment_history": profile.get("payment_history", []),
        "late_fee": profile["late_fee"],
        "outstanding_balance": profile.get("outstanding_balance", profile["late_fee"]),
        "total_overdue_charges": profile.get("total_overdue_charges", 0),
        "total_payments": profile.get("total_payments", 0),
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


def get_members_for_librarian_mongodb(session_id=None, query="", uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "librarian":
        raise ValueError("Only librarians can view members.")

    normalized_query = str(query or "").strip().lower()
    database = get_mongodb_database(uri=uri, db_name=db_name)
    members = [
        _read_profile_by_role(database, str(member.get("_id", "")).strip(), "member")
        for member in get_members_mongodb(uri=uri, db_name=db_name)
        if str(member.get("_id", "")).strip()
    ]

    if not normalized_query:
        return sorted(
            members,
            key=lambda member: (
                str(member.get("name", "")).lower(),
                str(member.get("_id", "")).lower()
            )
        )[:20]

    def score_member(member):
        member_id = str(member.get("_id", "")).lower()
        name = str(member.get("name", "")).lower()
        email = str(member.get("email", "")).lower()

        if normalized_query == member_id:
            return 0
        if normalized_query == email:
            return 1
        if normalized_query == name:
            return 2
        if member_id.startswith(normalized_query):
            return 3
        if name.startswith(normalized_query):
            return 4
        if email.startswith(normalized_query):
            return 5
        if normalized_query in member_id:
            return 6
        if normalized_query in name:
            return 7
        if normalized_query in email:
            return 8
        return None

    matches = []
    for member in members:
        score = score_member(member)
        if score is None:
            continue
        matches.append((score, member.get("name", ""), member.get("_id", ""), member))

    matches.sort(key=lambda item: (item[0], str(item[1]).lower(), str(item[2]).lower()))
    return [item[3] for item in matches[:20]]


def search_members_mongodb(session_id=None, query="", uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "librarian":
        raise ValueError("Only librarians can search members.")

    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return []

    database = get_mongodb_database(uri=uri, db_name=db_name)
    members = [
        _read_profile_by_role(database, str(member.get("_id", "")).strip(), "member")
        for member in get_members_mongodb(uri=uri, db_name=db_name)
        if str(member.get("_id", "")).strip()
    ]

    def score_member(member):
        member_id = str(member.get("_id", "")).lower()
        name = str(member.get("name", "")).lower()
        email = str(member.get("email", "")).lower()

        if normalized_query == member_id:
            return 0
        if normalized_query == email:
            return 1
        if normalized_query == name:
            return 2
        if member_id.startswith(normalized_query):
            return 3
        if name.startswith(normalized_query):
            return 4
        if email.startswith(normalized_query):
            return 5
        if normalized_query in member_id:
            return 6
        if normalized_query in name:
            return 7
        if normalized_query in email:
            return 8
        return None

    matches = []
    for member in members:
        score = score_member(member)
        if score is None:
            continue
        matches.append((score, member.get("name", ""), member.get("_id", ""), member))

    matches.sort(key=lambda item: (item[0], item[1].lower(), item[2].lower()))
    return [item[3] for item in matches[:20]]


def get_member_details_for_librarian_mongodb(session_id=None, member_id="", uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "librarian":
        raise ValueError("Only librarians can view member details.")

    normalized_member_id = str(member_id or "").strip()
    if not normalized_member_id:
        raise ValueError("Member ID is required.")

    database = get_mongodb_database(uri=uri, db_name=db_name)
    member = database["members"].find_one({"_id": normalized_member_id})
    if not member:
        raise ValueError("Member not found.")

    return _read_profile_by_role(database, normalized_member_id, "member")


def record_fee_payment_mongodb(session_id=None, member_id="", amount_paid=0, payment_method="counter_cash", notes="", uri=None, db_name=DEFAULT_DB_NAME):
    profile = get_current_profile_document_mongodb(session_id=session_id, uri=uri, db_name=db_name)
    if not profile:
        raise ValueError("No active session.")

    if profile.get("role") != "librarian":
        raise ValueError("Only librarians can process fee payments.")

    normalized_member_id = str(member_id or "").strip()
    if not normalized_member_id:
        raise ValueError("Member ID is required.")

    try:
        normalized_amount = round(float(amount_paid or 0), 2)
    except (TypeError, ValueError):
        raise ValueError("Payment amount must be a valid number.")

    if normalized_amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    normalized_method = str(payment_method or "").strip() or "counter_cash"
    normalized_notes = str(notes or "").strip()

    database = get_mongodb_database(uri=uri, db_name=db_name)
    member = database["members"].find_one({"_id": normalized_member_id})
    if not member:
        raise ValueError("Member not found.")

    recalculated_member = _read_profile_by_role(database, normalized_member_id, "member")
    outstanding_balance = round(float(recalculated_member.get("outstanding_balance", recalculated_member.get("late_fee", 0)) or 0), 2)
    if outstanding_balance <= 0:
        raise ValueError("This member does not have an outstanding late-fee balance.")

    if normalized_amount > outstanding_balance:
        raise ValueError(f"Payment cannot exceed the outstanding balance of ${outstanding_balance:.2f}.")

    payment_count = database["fee_payments"].count_documents({})
    payment_id = f"P{str(payment_count + 1).zfill(3)}"
    receipt_no = f"RCP-{str(payment_count + 1).zfill(4)}"
    processed_at = _format_session_datetime(_utc_now())

    payment_document = {
        "_id": payment_id,
        "member_id": normalized_member_id,
        "member_name": recalculated_member.get("name") or member.get("name"),
        "amount_paid": normalized_amount,
        "payment_method": normalized_method,
        "processed_by": profile.get("_id"),
        "processed_at": processed_at,
        "notes": normalized_notes,
        "receipt_no": receipt_no
    }
    insert_result = database["fee_payments"].insert_one(payment_document)
    if not insert_result or insert_result.inserted_id != payment_id:
        raise RuntimeError("Fee payment could not be recorded.")

    expected_outstanding_balance = round(max(0, outstanding_balance - normalized_amount), 2)
    member_update_result = database["members"].update_one(
        {"_id": normalized_member_id},
        {"$set": {"late_fee": expected_outstanding_balance}}
    )
    if member_update_result.matched_count <= 0:
        raise RuntimeError("Fee payment was recorded, but the member balance could not be updated.")

    inserted_payment = database["fee_payments"].find_one({"_id": payment_id})
    if not inserted_payment:
        raise RuntimeError("Fee payment record was not found after insertion.")

    updated_member = _read_profile_by_role(database, normalized_member_id, "member")
    new_outstanding_balance = round(float(updated_member.get("outstanding_balance", updated_member.get("late_fee", 0)) or 0), 2)

    if new_outstanding_balance != expected_outstanding_balance:
        raise RuntimeError(
            f"Fee payment was recorded, but the outstanding balance expected ${expected_outstanding_balance:.2f} and is ${new_outstanding_balance:.2f}."
        )

    _invalidate_runtime_caches(uri=uri, db_name=db_name)
    return {
        "payment": payment_document,
        "member": updated_member,
        "previous_outstanding_balance": outstanding_balance,
        "new_outstanding_balance": new_outstanding_balance
    }


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
    loan_date = _utc_now()
    
    # Calculate next loan number globally using MongoDB data
    all_members = list(database["members"].find())
    next_loan_number = sum(len(_normalize_loan_entries(m.get("current_loans"))) + len(_normalize_loan_entries(m.get("loan_history"))) for m in all_members) + 1
    
    normalized_current_loans = _normalize_loan_entries(member.get("current_loans"))
    normalized_current_loans.append({
        "copy_no": copy_no,
        "book_title": book["title"],
        "loan_id": f"L{next_loan_number:03d}",
        "loan_date": _format_loan_day(loan_date),
        "due_date": _format_loan_day(loan_date + timedelta(days=14))
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


def _find_member_with_copy(copy_no):
    database = get_mongodb_database()
    for member in database["members"].find():
        normalized_member = _normalize_profile_document(member, fallback_id=member.get("_id"))
        current_loans = _normalize_loan_entries(normalized_member.get("current_loans"))
        if any(loan.get("copy_no") == copy_no for loan in current_loans):
            return normalized_member
    return None


def return_book(copy_no, member_id=None):
    database = get_mongodb_database()
    member = _find_member(member_id) if member_id else None

    if member:
        normalized_current_loans = _normalize_loan_entries(member.get("current_loans"))
        if not any(loan.get("copy_no") == copy_no for loan in normalized_current_loans):
            member = None

    if not member:
        member = _find_member_with_copy(copy_no)

    if not member:
        return "No member currently has this copy loaned"

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
    returned_loan["return_date"] = _format_loan_day(_utc_now())
    normalized_history.append(returned_loan)
    member["loan_history"] = normalized_history
    copy["status"] = "Available"

    try:
        if MongoClient is not None:
            database["members"].update_one({"_id": member["_id"]}, {"$set": {
                "current_loans": normalized_current_loans,
                "loan_history": normalized_history
            }})
            database["books"].update_one({"_id": book["_id"], "copies.copy_no": copy_no}, {"$set": {"copies.$.status": "Available"}})
            database["loan_requests"].delete_many({
                "member_id": member["_id"],
                "copy_no": copy_no
            })
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
            reset_data_from_mongodb()

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
