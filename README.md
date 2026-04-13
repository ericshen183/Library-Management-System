# Library-Management-System

# Running Tests

This project uses [pytest](https://pytest.org/) for automated testing.

## To run all tests:

```
pytest
```

## Test Coverage
- User authentication (login, failure cases)
- Book search and catalog
- Issuing and returning books
- Managing user accounts and loan requests
- Fee payment and late fee calculation

## Example Test Output
Pytest will display a summary of passed/failed tests and details for any failures.

## Adding More Tests
- Add new test functions to `test_library.py` following the existing examples.
- Use descriptive names and docstrings for clarity.

---

# Installing dependencies
pip install -r requirements.txt

# To Start Server
python server.py