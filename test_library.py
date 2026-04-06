from library import login, issue_book, return_book, books


def test_login_success():
    assert login("student1", "pass123") == True

def test_login_fail():
    assert login("student1", "wrongpass") == False



def test_issue_book_success():
    result = issue_book("B001", "student1")
    assert result == "Book issued successfully"

def test_issue_book_not_exist():
    result = issue_book("B999", "student1")
    assert result == "Book does not exist"

def test_issue_book_unavailable():
    issue_book("B002", "student1")
    result = issue_book("B002", "student2")
    assert result == "Book not available"


def test_return_book_success():
    issue_book("B003", "student1")
    result = return_book("B003")
    assert result == "Book returned successfully"

def test_return_book_not_issued():
    result = return_book("B999")
    assert result == "Book was not issued"