from library import books, issue_book, login, members, reset_data, return_book


def setup_function():
    reset_data()


def test_login_success():
    assert login("M001", "member001") is True


def test_login_fail():
    assert login("M001", "wrongpass") is False


def test_librarian_login_success():
    assert login("L001", "librarian001") is True


def test_issue_book_success():
    result = issue_book("C003", "M004")
    assert result == "Book issued successfully"
    issued_copy = next(copy for book in books for copy in book["copies"] if copy["copy_no"] == "C003")
    assert issued_copy["status"] == "On Loan"


def test_issue_book_not_exist():
    result = issue_book("C999", "M004")
    assert result == "Book copy does not exist"


def test_issue_book_unavailable():
    result = issue_book("C002", "M004")
    assert result == "Book not available"


def test_return_book_success():
    result = return_book("C002", "M001")
    assert result == "Book returned successfully"
    returned_copy = next(copy for book in books for copy in book["copies"] if copy["copy_no"] == "C002")
    assert returned_copy["status"] == "Available"
    member = next(member for member in members if member["_id"] == "M001")
    assert all(loan["copy_no"] != "C002" for loan in member["current_loans"])


def test_return_book_not_issued():
    result = return_book("C003", "M004")
    assert result == "Book was not issued to this member"
