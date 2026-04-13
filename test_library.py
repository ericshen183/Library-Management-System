from copy import deepcopy
from datetime import datetime, timezone
import re
from datetime import timedelta

import pytest

import library


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, matched_count=0, modified_count=0):
        self.matched_count = matched_count
        self.modified_count = modified_count


class FakeDeleteResult:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [deepcopy(document) for document in (documents or [])]

    def find_one(self, query=None):
        query = query or {}
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query=None):
        query = query or {}
        return [deepcopy(document) for document in self.documents if _matches(document, query)]

    def count_documents(self, query=None):
        query = query or {}
        return sum(1 for document in self.documents if _matches(document, query))

    def insert_one(self, document):
        self.documents.append(deepcopy(document))
        return FakeInsertResult(document.get("_id"))

    def update_one(self, query, update):
        for index, document in enumerate(self.documents):
            if not _matches(document, query):
                continue

            updated_document = deepcopy(document)
            if "$set" in update:
                for dotted_key, value in update["$set"].items():
                    _set_nested_value(updated_document, dotted_key, value)
            self.documents[index] = updated_document
            return FakeUpdateResult(matched_count=1, modified_count=1)

        return FakeUpdateResult(matched_count=0, modified_count=0)

    def delete_many(self, query):
        kept_documents = []
        deleted_count = 0
        for document in self.documents:
            if _matches(document, query):
                deleted_count += 1
            else:
                kept_documents.append(document)
        self.documents = kept_documents
        return FakeDeleteResult(deleted_count)


class FakeDatabase(dict):
    def __getitem__(self, item):
        return super().__getitem__(item)


def _matches(document, query):
    if not query:
        return True

    for key, expected in query.items():
        if key == "$or":
            return any(_matches(document, branch) for branch in expected)

        actual = _get_nested_value(document, key)
        if actual != expected:
            return False

    return True


def _get_nested_value(document, dotted_key):
    current = document
    for part in dotted_key.split("."):
        if isinstance(current, list):
            if part == "$":
                return current
            current = next((item for item in current if isinstance(item, dict) and part in item), None)
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _set_nested_value(document, dotted_key, value):
    if ".$." in dotted_key:
        prefix, suffix = dotted_key.split(".$.", 1)
        collection_name = prefix.split(".")[0]
        if collection_name not in document or not isinstance(document[collection_name], list):
            return
        target_copy_no = document.get("copies", [{}])[0].get("copy_no")
        for item in document[collection_name]:
            if isinstance(item, dict) and item.get("copy_no") == target_copy_no:
                item[suffix] = value
                return
        return

    current = document
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


@pytest.fixture
def fake_library_db():
    return FakeDatabase({
        "accounts": FakeCollection([
            {
                "account_id": "A001",
                "user_id": "M001",
                "role": "member",
                "login_id": "M001",
                "password_hash": library.double_hash("member001"),
                "account_status": "active"
            },
            {
                "account_id": "A900",
                "user_id": "L001",
                "role": "librarian",
                "login_id": "L001",
                "password_hash": library.double_hash("librarian001"),
                "account_status": "active"
            }
        ]),
        "members": FakeCollection([
            {
                "_id": "M001",
                "name": "John Smith",
                "email": "john.smith@email.com",
                "current_loans": [],
                "loan_history": [],
                "late_fee": 0.0
            }
        ]),
        "librarians": FakeCollection([
            {
                "_id": "L001",
                "name": "Lib One",
                "email": "lib.one@email.com",
                "current_loans": [],
                "loan_history": [],
                "late_fee": 0.0
            }
        ]),
        "books": FakeCollection([
            {
                "_id": "9780743273565",
                "title": "The Great Gatsby",
                "copies": [
                    {"copy_no": "C001", "status": "Available"},
                    {"copy_no": "C002", "status": "On Loan"}
                ]
            }
        ]),
        "loan_requests": FakeCollection([]),
        "fee_payments": FakeCollection([])
    })


@pytest.fixture
def fake_payment_db():
    return FakeDatabase({
        "members": FakeCollection([
            {
                "_id": "M003",
                "name": "Bob Johnson",
                "email": "bob.johnson@email.com",
                "current_loans": [
                    {
                        "copy_no": "C001",
                        "book_title": "The Great Gatsby",
                        "loan_id": "L003",
                        "loan_date": "2026-03-29",
                        "due_date": "2026-04-04"
                    }
                ],
                "loan_history": [
                    {
                        "copy_no": "C009",
                        "book_title": "Old Loan",
                        "loan_id": "L001",
                        "loan_date": "2026-02-01",
                        "due_date": "2026-02-15",
                        "return_date": "2026-02-14"
                    }
                ],
                "late_fee": 10.0
            }
        ]),
        "fee_payments": FakeCollection([]),
        "loan_requests": FakeCollection([]),
        "books": FakeCollection([])
    })


def test_apply_member_fine_metrics_reduces_outstanding_balance_with_payments(monkeypatch):
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, tzinfo=timezone.utc))

    profile = {
        "_id": "M003",
        "name": "Bob Johnson",
        "email": "bob.johnson@email.com",
        "current_loans": [
            {
                "copy_no": "C001",
                "book_title": "The Great Gatsby",
                "loan_id": "L003",
                "loan_date": "2026-03-29",
                "due_date": "2026-04-04"
            }
        ],
        "loan_history": [],
        "late_fee": 5.0,
        "payment_history": [
            {
                "_id": "P001",
                "member_id": "M003",
                "member_name": "Bob Johnson",
                "amount_paid": 5.0,
                "payment_method": "counter_cash",
                "processed_by": "L001",
                "processed_at": "2026-04-09T15:33:39Z",
                "notes": "",
                "receipt_no": "RCP-0001"
            }
        ]
    }

    result = library._apply_member_fine_metrics(profile)

    assert result["total_overdue_charges"] == 10.0
    assert result["total_payments"] == 5.0
    assert result["late_fee"] == 5.0
    assert result["outstanding_balance"] == 5.0
    assert result["current_loans"][0]["fine_amount"] == 10.0
    assert result["current_loans"][0]["outstanding_fine_amount"] == 5.0


def test_login_success_member(monkeypatch, fake_library_db):
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_library_db)
    monkeypatch.setattr(
        library,
        "_read_profile_by_role",
        lambda database, user_id, role: {
            "_id": user_id,
            "name": "John Smith" if user_id == "M001" else "Lib One",
            "email": "john.smith@email.com" if user_id == "M001" else "lib.one@email.com",
            "current_loans": [],
            "loan_history": [],
            "late_fee": 0.0
        }
    )

    result = library.authenticate_user_mongodb("M001", "member001")

    assert result is not None
    assert result["user_id"] == "M001"
    assert result["role"] == "member"


def test_login_fail_wrong_password(monkeypatch, fake_library_db):
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_library_db)

    result = library.authenticate_user_mongodb("M001", "wrongpass")

    assert result is None


def test_login_success_librarian(monkeypatch, fake_library_db):
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_library_db)
    monkeypatch.setattr(
        library,
        "_read_profile_by_role",
        lambda database, user_id, role: {
            "_id": user_id,
            "name": "Lib One",
            "email": "lib.one@email.com",
            "current_loans": [],
            "loan_history": [],
            "late_fee": 0.0
        }
    )

    result = library.authenticate_user_mongodb("L001", "librarian001")

    assert result is not None
    assert result["user_id"] == "L001"
    assert result["role"] == "librarian"


@pytest.mark.parametrize(
    ("login_id", "password", "expected_message"),
    [
        ("M001", "wrongpass", "Wrong password should fail member login"),
        ("UNKNOWN", "member001", "Unknown login ID should fail authentication"),
    ],
    ids=["login-fail-wrong-password", "login-fail-unknown-user"]
)
def test_login_fail_cases(monkeypatch, fake_library_db, login_id, password, expected_message):
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_library_db)

    result = library.authenticate_user_mongodb(login_id, password)

    assert result is None, expected_message


def test_issue_book_success(monkeypatch, fake_library_db):
    monkeypatch.setattr(library, "get_mongodb_database", lambda: fake_library_db)
    monkeypatch.setattr(
        library,
        "_find_member",
        lambda member_id: fake_library_db["members"].find_one({"_id": member_id})
    )
    monkeypatch.setattr(
        library,
        "_find_copy",
        lambda copy_no: (
            fake_library_db["books"].find_one({"_id": "9780743273565"}),
            {"copy_no": "C001", "status": "Available"}
        )
    )
    monkeypatch.setattr(library, "_invalidate_runtime_caches", lambda uri=None, db_name=None: None)
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, tzinfo=timezone.utc))

    result = library.issue_book("C001", "M001")

    assert result == "Book issued successfully"


def test_issue_book_unavailable(monkeypatch, fake_library_db):
    monkeypatch.setattr(library, "get_mongodb_database", lambda: fake_library_db)
    monkeypatch.setattr(
        library,
        "_find_member",
        lambda member_id: fake_library_db["members"].find_one({"_id": member_id})
    )
    monkeypatch.setattr(
        library,
        "_find_copy",
        lambda copy_no: (
            fake_library_db["books"].find_one({"_id": "9780743273565"}),
            {"copy_no": "C002", "status": "On Loan"}
        )
    )

    result = library.issue_book("C002", "M001")

    assert result == "Book not available"


@pytest.mark.parametrize(
    ("copy_no", "member_id", "expected_result"),
    [
        ("C999", "M001", "Book copy does not exist"),
        ("C001", "M404", "Member does not exist"),
    ],
    ids=["loan-fail-copy-missing", "loan-fail-member-missing"]
)
def test_issue_book_fail_cases(monkeypatch, fake_library_db, copy_no, member_id, expected_result):
    monkeypatch.setattr(library, "get_mongodb_database", lambda: fake_library_db)
    monkeypatch.setattr(
        library,
        "_find_member",
        lambda lookup_member_id: fake_library_db["members"].find_one({"_id": lookup_member_id})
    )

    if copy_no == "C999":
        monkeypatch.setattr(library, "_find_copy", lambda requested_copy_no: (None, None))
    else:
        monkeypatch.setattr(
            library,
            "_find_copy",
            lambda requested_copy_no: (
                fake_library_db["books"].find_one({"_id": "9780743273565"}),
                {"copy_no": "C001", "status": "Available"}
            )
        )

    result = library.issue_book(copy_no, member_id)

    assert result == expected_result, f"Expected loan failure output '{expected_result}'"


def test_create_loan_request_blocks_member_with_unpaid_late_fees(monkeypatch):
    monkeypatch.setattr(
        library,
        "get_current_profile_document_mongodb",
        lambda session_id=None, uri=None, db_name=None: {
            "_id": "M003",
            "role": "member",
            "current_loans": [],
            "late_fee": 5.0
        }
    )

    with pytest.raises(ValueError, match="unpaid late fees"):
        library.create_loan_request_mongodb("session-123", "9780743273565", "C001")


@pytest.mark.parametrize(
    ("member_profile", "expected_message"),
    [
        (
            {"_id": "L001", "role": "librarian", "current_loans": [], "late_fee": 0},
            "Only members can request loans."
        ),
        (
            {"_id": "M003", "role": "member", "current_loans": [], "late_fee": 4.0},
            "Members with unpaid late fees cannot request another book."
        ),
    ],
    ids=["loan-request-fail-librarian", "loan-request-fail-unpaid-fees"]
)
def test_create_loan_request_fail_cases(monkeypatch, member_profile, expected_message):
    monkeypatch.setattr(
        library,
        "get_current_profile_document_mongodb",
        lambda session_id=None, uri=None, db_name=None: member_profile
    )

    with pytest.raises(ValueError, match=expected_message):
        library.create_loan_request_mongodb("session-123", "9780743273565", "C001")


def test_record_fee_payment_creates_fee_payment_and_updates_member_balance(monkeypatch, fake_payment_db):
    monkeypatch.setattr(
        library,
        "get_current_profile_document_mongodb",
        lambda session_id=None, uri=None, db_name=None: {"_id": "L001", "role": "librarian"}
    )
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_payment_db)
    monkeypatch.setattr(library, "_invalidate_runtime_caches", lambda uri=None, db_name=None: None)
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, 15, 33, 39, tzinfo=timezone.utc))

    real_read_profile = library._read_profile_by_role

    def fake_read_profile(database, user_id, role):
        if role != "member":
            return {"_id": user_id, "role": role}
        member_document = database["members"].find_one({"_id": user_id})
        member_document["payment_history"] = database["fee_payments"].find({"member_id": user_id})
        return library._apply_member_fine_metrics(member_document)

    monkeypatch.setattr(library, "_read_profile_by_role", fake_read_profile)

    result = library.record_fee_payment_mongodb(
        session_id="session-123",
        member_id="M003",
        amount_paid=5.0,
        payment_method="counter_cash",
        notes="Front desk payment"
    )

    assert result["previous_outstanding_balance"] == 10.0
    assert result["new_outstanding_balance"] == 5.0

    stored_payment = fake_payment_db["fee_payments"].find_one({"_id": "P001"})
    assert stored_payment is not None
    assert stored_payment["member_id"] == "M003"
    assert stored_payment["amount_paid"] == 5.0
    assert stored_payment["processed_by"] == "L001"

    stored_member = fake_payment_db["members"].find_one({"_id": "M003"})
    assert stored_member["late_fee"] == 5.0

    monkeypatch.setattr(library, "_read_profile_by_role", real_read_profile)


@pytest.mark.parametrize(
    ("amount_paid", "expected_message"),
    [
        (0, "Payment amount must be greater than zero."),
        (11.0, "Payment cannot exceed the outstanding balance of $10.00."),
    ],
    ids=["payment-fail-zero-amount", "payment-fail-overpay"]
)
def test_record_fee_payment_fail_cases(monkeypatch, fake_payment_db, amount_paid, expected_message):
    monkeypatch.setattr(
        library,
        "get_current_profile_document_mongodb",
        lambda session_id=None, uri=None, db_name=None: {"_id": "L001", "role": "librarian"}
    )
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: fake_payment_db)
    monkeypatch.setattr(library, "_invalidate_runtime_caches", lambda uri=None, db_name=None: None)

    def fake_read_profile(database, user_id, role):
        if role != "member":
            return {"_id": user_id, "role": role}
        member_document = database["members"].find_one({"_id": user_id})
        member_document["payment_history"] = database["fee_payments"].find({"member_id": user_id})
        return library._apply_member_fine_metrics(member_document)

    monkeypatch.setattr(library, "_read_profile_by_role", fake_read_profile)

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        library.record_fee_payment_mongodb(
            session_id="session-123",
            member_id="M003",
            amount_paid=amount_paid,
            payment_method="counter_cash",
            notes="Invalid payment path"
        )

    assert fake_payment_db["fee_payments"].count_documents({}) == 0, "No payment document should be inserted on validation failure"


def test_search_members_uses_payment_aware_member_profile(monkeypatch):
    monkeypatch.setattr(
        library,
        "get_current_profile_document_mongodb",
        lambda session_id=None, uri=None, db_name=None: {"_id": "L001", "role": "librarian"}
    )
    monkeypatch.setattr(
        library,
        "get_members_mongodb",
        lambda uri=None, db_name=None: [
            {"_id": "M003", "name": "Bob Johnson", "email": "bob.johnson@email.com", "late_fee": 10.0}
        ]
    )
    monkeypatch.setattr(library, "get_mongodb_database", lambda uri=None, db_name=None: FakeDatabase({}))
    monkeypatch.setattr(
        library,
        "_read_profile_by_role",
        lambda database, user_id, role: {
            "_id": "M003",
            "name": "Bob Johnson",
            "email": "bob.johnson@email.com",
            "current_loans": [],
            "loan_history": [],
            "payment_history": [
                {
                    "_id": "P001",
                    "member_id": "M003",
                    "amount_paid": 5.0
                }
            ],
            "late_fee": 5.0,
            "outstanding_balance": 5.0,
            "total_overdue_charges": 10.0,
            "total_payments": 5.0
        }
    )

    result = library.search_members_mongodb(session_id="session-123", query="M003")

    assert len(result) == 1
    assert result[0]["late_fee"] == 5.0
    assert result[0]["outstanding_balance"] == 5.0
    assert result[0]["total_payments"] == 5.0


@pytest.mark.parametrize(
    ("profile", "expected_late_fee", "expected_outstanding_fine"),
    [
        (
            {
                "_id": "M010",
                "current_loans": [
                    {
                        "copy_no": "C010",
                        "book_title": "Late Book",
                        "loan_id": "L010",
                        "loan_date": "2026-03-20",
                        "due_date": "2026-04-04"
                    }
                ],
                "loan_history": [],
                "late_fee": 10.0,
                "payment_history": []
            },
            10.0,
            10.0
        ),
        (
            {
                "_id": "M011",
                "current_loans": [
                    {
                        "copy_no": "C011",
                        "book_title": "Paid Down Book",
                        "loan_id": "L011",
                        "loan_date": "2026-03-20",
                        "due_date": "2026-04-04"
                    }
                ],
                "loan_history": [],
                "late_fee": 5.0,
                "payment_history": [
                    {
                        "_id": "P050",
                        "member_id": "M011",
                        "amount_paid": 5.0,
                        "processed_at": "2026-04-09T15:33:39Z"
                    }
                ]
            },
            5.0,
            5.0
        ),
    ],
    ids=["late-fee-fail-no-payment-applied", "late-fee-pass-payment-applied"]
)
def test_late_fee_case_outputs(monkeypatch, profile, expected_late_fee, expected_outstanding_fine):
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, tzinfo=timezone.utc))

    result = library._apply_member_fine_metrics(profile)

    assert result["late_fee"] == expected_late_fee, "Late fee output should match the expected account balance for this case"
    assert result["current_loans"][0]["outstanding_fine_amount"] == expected_outstanding_fine, "Loan card outstanding fine should reflect payments applied"


def test_return_book_removes_matching_loan_request(monkeypatch):
    fake_db = FakeDatabase({
        "members": FakeCollection([
            {
                "_id": "M001",
                "name": "John Smith",
                "current_loans": [
                    {
                        "copy_no": "C002",
                        "book_title": "To Kill a Mockingbird",
                        "loan_id": "L001",
                        "loan_date": "2026-03-20",
                        "due_date": "2026-04-03"
                    }
                ],
                "loan_history": []
            }
        ]),
        "books": FakeCollection([
            {
                "_id": "9780061120084",
                "title": "To Kill a Mockingbird",
                "copies": [{"copy_no": "C002", "status": "On Loan"}]
            }
        ]),
        "loan_requests": FakeCollection([
            {"_id": "R001", "member_id": "M001", "copy_no": "C002", "status": "issued"}
        ])
    })

    monkeypatch.setattr(library, "get_mongodb_database", lambda: fake_db)
    monkeypatch.setattr(
        library,
        "_find_member",
        lambda member_id: fake_db["members"].find_one({"_id": member_id})
    )
    monkeypatch.setattr(library, "_find_member_with_copy", lambda copy_no: None)
    monkeypatch.setattr(
        library,
        "_find_copy",
        lambda copy_no: (
            fake_db["books"].find_one({"_id": "9780061120084"}),
            {"copy_no": "C002", "status": "On Loan"}
        )
    )
    monkeypatch.setattr(library, "_invalidate_runtime_caches", lambda uri=None, db_name=None: None)
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, tzinfo=timezone.utc))

    result = library.return_book("C002", "M001")

    assert result == "Book returned successfully"
    assert fake_db["loan_requests"].find_one({"_id": "R001"}) is None
    updated_member = fake_db["members"].find_one({"_id": "M001"})
    assert updated_member["current_loans"] == []
    assert updated_member["loan_history"][0]["copy_no"] == "C002"


@pytest.mark.parametrize(
    ("copy_no", "member_id", "expected_result"),
    [
        ("C404", "M001", "No member currently has this copy loaned"),
        ("C001", "M001", "No member currently has this copy loaned"),
    ],
    ids=["return-fail-copy-not-loaned", "return-fail-member-does-not-have-copy"]
)
def test_return_book_fail_cases(monkeypatch, copy_no, member_id, expected_result):
    monkeypatch.setattr(library, "get_mongodb_database", lambda: FakeDatabase({}))
    monkeypatch.setattr(
        library,
        "_find_member",
        lambda requested_member_id: {
            "_id": "M001",
            "current_loans": [],
            "loan_history": []
        } if requested_member_id == "M001" else None
    )
    monkeypatch.setattr(library, "_find_member_with_copy", lambda requested_copy_no: None)
    monkeypatch.setattr(library, "_find_copy", lambda requested_copy_no: (None, None))

    result = library.return_book(copy_no, member_id)

    assert result == expected_result, f"Expected return-book failure output '{expected_result}'"


@pytest.mark.xfail(
    reason="Intentional example: this asserts the pre-payment late fee instead of the reduced outstanding balance after payment.",
    strict=True
)
def test_intentional_failure_payment_does_not_reduce_balance(monkeypatch):
    # This test is intentionally wrong.
    # After a $5 payment against a $10 outstanding balance, the system should
    # reduce late_fee/outstanding_balance to $5. This assertion expects the old
    # unpaid balance of $10, so pytest should mark this case as an expected fail.
    monkeypatch.setattr(library, "_utc_now", lambda: datetime(2026, 4, 9, tzinfo=timezone.utc))

    profile = {
        "_id": "M003",
        "name": "Bob Johnson",
        "email": "bob.johnson@email.com",
        "current_loans": [
            {
                "copy_no": "C001",
                "book_title": "The Great Gatsby",
                "loan_id": "L003",
                "loan_date": "2026-03-29",
                "due_date": "2026-04-04"
            }
        ],
        "loan_history": [],
        "late_fee": 5.0,
        "payment_history": [
            {
                "_id": "P001",
                "member_id": "M003",
                "member_name": "Bob Johnson",
                "amount_paid": 5.0,
                "payment_method": "counter_cash",
                "processed_by": "L001",
                "processed_at": "2026-04-09T15:33:39Z",
                "notes": "",
                "receipt_no": "RCP-0001"
            }
        ]
    }

    result = library._apply_member_fine_metrics(profile)

    # Intentionally incorrect expectation:
    # the real value should be 5.0 because the payment was already applied.
    assert result["late_fee"] == 10.0


# --- Late Fee Calculation Tests ---

def test_calculate_days_late_on_time():
    """Test that no late days are counted if returned on due date."""
    due_date = "2026-04-10"
    comparison_date = "2026-04-10"
    assert library._calculate_days_late(due_date, comparison_date) == 0

def test_calculate_days_late_late_by_3_days():
    """Test that 3 days late is calculated correctly."""
    due_date = "2026-04-10"
    comparison_date = "2026-04-13"
    assert library._calculate_days_late(due_date, comparison_date) == 3

def test_calculate_days_late_before_due():
    """Test that returning before due date counts as 0 late days."""
    due_date = "2026-04-10"
    comparison_date = "2026-04-08"
    assert library._calculate_days_late(due_date, comparison_date) == 0

def test_calculate_fine_amount_zero_days():
    """Test that no fine is charged for 0 late days."""
    assert library._calculate_fine_amount(0) == 0.0

def test_calculate_fine_amount_positive_days():
    """Test that fine is correct for positive late days (3 days)."""
    assert library._calculate_fine_amount(3) == 6.0  # 3 days * DAILY_FINE_RATE (2.0)

def test_annotate_loan_record_on_time():
    """Test that a loan returned on time is not overdue and has no fine."""
    loan = {"due_date": "2026-04-10"}
    annotated = library._annotate_loan_record(loan)
    assert annotated["overdue_days"] == 0
    assert annotated["fine_amount"] == 0.0
    assert annotated["is_overdue"] is False

def test_annotate_loan_record_late():
    """Test that a loan returned 3 days late is marked overdue and fined correctly."""
    due_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    loan = {"due_date": due_date}
    annotated = library._annotate_loan_record(loan)
    assert annotated["overdue_days"] == 3
    assert annotated["fine_amount"] == 6.0
    assert annotated["is_overdue"] is True

# --- End Late Fee Calculation Tests ---
