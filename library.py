users = {
    "student1": "pass123",
    "student2": "pass456",
    "librarian": "admin123"
}

books = {
    "B001": {"title": "Python Basics", "available": True},
    "B002": {"title": "Data Structures", "available": True},
    "B003": {"title": "Algorithms", "available": True}
}

issued_books = {}


def login(username, password):
    if username in users and users[username] == password:
        return True
    return False


def issue_book(book_id, username):
   
    if book_id not in books:
        return "Book does not exist"

    
    if not books[book_id]["available"]:
        return "Book not available"

    books[book_id]["available"] = False
    issued_books[book_id] = username
    return "Book issued successfully"


def return_book(book_id):
   
    if book_id not in issued_books:
        return "Book was not issued"

    
    books[book_id]["available"] = True
    del issued_books[book_id]
    return "Book returned successfully"


# demo
def main():
    print("=== Library System ===")

    username = input("Enter username: ")
    password = input("Enter password: ")

    if not login(username, password):
        print("Login failed!")
        return

    print("Login successful!")

    while True:
        print("\n1. Issue Book")
        print("2. Return Book")
        print("3. Exit")

        choice = input("Choose option: ")

        if choice == "1":
            book_id = input("Enter Book ID: ")
            print(issue_book(book_id, username))

        elif choice == "2":
            book_id = input("Enter Book ID: ")
            print(return_book(book_id))

        elif choice == "3":
            print("Goodbye!")
            break

        else:
            print("Invalid choice!")


if __name__ == "__main__":
    main()