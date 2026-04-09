import json
import atexit
import signal
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

from library import (
    authenticate_user_mongodb,
    heartbeat_user_session_mongodb,
    clear_all_user_sessions_mongodb,
    has_active_user_session_mongodb,
    clear_user_session_mongodb,
    clear_user_sessions_for_user_mongodb,
    create_loan_request_mongodb,
    create_user_session_mongodb,
    get_books_mongodb,
    get_current_loans_mongodb,
    get_current_profile_document_mongodb,
    get_current_session_mongodb,
    get_member_details_for_librarian_mongodb,
    get_members_for_librarian_mongodb,
    get_next_member_id_mongodb,
    get_library_page_data_mongodb,
    get_librarian_loans_overview_mongodb,
    issue_loan_request_mongodb,
    record_fee_payment_mongodb,
    register_member_mongodb,
    return_book,
    search_members_mongodb,
    update_login_status_mongodb,
    update_session_current_page_mongodb,
    update_user_current_page_mongodb,
    warm_mongodb_connection,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR
SESSION_COOKIE_NAME = "library_session_id"
_shutdown_started = False


class LibraryRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self._cookie_headers = []
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self):
        for cookie_header in self._cookie_headers:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _get_session_id(self):
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None

        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _set_session_cookie(self, session_id):
        self._cookie_headers.append(f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax")

    def _clear_session_cookie(self):
        self._cookie_headers.append(f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def _read_request_payload(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        if not raw_body:
            return {}

        decoded_body = raw_body.decode("utf-8").strip()
        if not decoded_body:
            return {}

        try:
            return json.loads(decoded_body)
        except json.JSONDecodeError:
            parsed = parse_qs(decoded_body, keep_blank_values=True)
            if parsed:
                return {
                    key: values[0] if isinstance(values, list) and values else values
                    for key, values in parsed.items()
                }
            return {}

    def do_POST(self):
        request_path = urlparse(self.path).path

        if request_path == "/api/login":
            self._handle_login()
            return

        if request_path == "/api/logout":
            self._handle_logout()
            return

        if request_path == "/api/register":
            self._handle_register()
            return

        if request_path == "/api/session/page":
            self._handle_page_update()
            return

        if request_path == "/api/session/ping":
            self._handle_session_ping()
            return

        if request_path == "/api/loan-requests":
            self._handle_loan_request()
            return

        if request_path == "/api/return-book":
            self._handle_return_book()
            return

        if request_path == "/api/issue-book-request":
            self._handle_issue_book_request()
            return

        if request_path == "/api/fee-payments":
            self._handle_fee_payment()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_GET(self):
        request_path = urlparse(self.path).path

        if request_path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/LoginPage/Login.html")
            self.end_headers()
            return

        if request_path == "/api/logout":
            self._handle_logout()
            return

        if request_path == "/api/session":
            self._handle_session()
            return

        if request_path == "/api/account":
            self._handle_account()
            return

        if request_path == "/api/books":
            self._handle_books()
            return

        if request_path == "/api/members":
            self._handle_members()
            return

        if request_path == "/api/library-data":
            self._handle_library_data()
            return

        if request_path == "/api/loans":
            self._handle_loans()
            return

        if request_path == "/api/librarian-loans":
            self._handle_librarian_loans()
            return

        if request_path == "/api/next-member-id":
            self._handle_next_member_id()
            return

        if request_path == "/api/member-search":
            self._handle_member_search()
            return

        if request_path == "/api/member-details":
            self._handle_member_details()
            return

        super().do_GET()

    def _handle_login(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))

            login_id = str(payload.get("loginId", "")).strip()
            password = str(payload.get("password", "")).strip()

            if not login_id or not password:
                self._send_json({"ok": False, "message": "Login ID and password are required."}, status=HTTPStatus.BAD_REQUEST)
                return

            user = authenticate_user_mongodb(login_id, password)
            if not user:
                existing_session_id = self._get_session_id()
                if existing_session_id:
                    clear_user_session_mongodb(existing_session_id)
                self._clear_session_cookie()
                self._send_json({"ok": False, "message": "Invalid login ID or password."}, status=HTTPStatus.UNAUTHORIZED)
                return

            session_record = create_user_session_mongodb(user)
            self._set_session_cookie(session_record["session_id"])
            self._send_json({
                "ok": True,
                "source": "mongodb",
                "user": user,
                "sessionId": session_record["session_id"],
                "redirectPath": session_record["current_page"]
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"MongoDB login failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_session(self):
        try:
            user = get_current_session_mongodb(session_id=self._get_session_id())

            if not user:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({
                "ok": True,
                "source": "mongodb",
                "user": user,
                "sessionId": self._get_session_id(),
                "currentPage": user.get("current_page")
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Session lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_account(self):
        try:
            profile = get_current_profile_document_mongodb(session_id=self._get_session_id())
            if not profile:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            collection_name = "members" if profile.get("role") == "member" else "librarians"
            self._send_json({"ok": True, "source": "mongodb", "collection": collection_name, "user": profile})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Account lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_books(self):
        try:
            books = get_books_mongodb()
            self._send_json({"ok": True, "source": "mongodb", "collection": "books", "books": books})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Book lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_members(self):
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query).get("q", [""])[0]
            members = get_members_for_librarian_mongodb(session_id=self._get_session_id(), query=query)
            self._send_json({"ok": True, "source": "mongodb", "collection": "members", "members": members})
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Members lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_library_data(self):
        try:
            library_data = get_library_page_data_mongodb(session_id=self._get_session_id())
            if not library_data:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "books",
                "user": library_data["user"],
                "books": library_data["books"]
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Library lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_loans(self):
        try:
            loans_data = get_current_loans_mongodb(session_id=self._get_session_id())
            if not loans_data:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "members",
                "user": loans_data["user"],
                "current_loans": loans_data["current_loans"],
                "loan_history": loans_data["loan_history"]
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Loan lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_librarian_loans(self):
        try:
            overview = get_librarian_loans_overview_mongodb(session_id=self._get_session_id())

            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "loan_requests",
                "user": overview.get("user"),
                "loan_requests": overview.get("loan_requests", []),
                "pending_requests": overview.get("pending_requests", []),
                "totalCount": len(overview.get("loan_requests", [])),
                "pendingCount": len(overview.get("pending_requests", []))
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Librarian loan overview failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_logout(self):
        try:
            payload = self._read_request_payload()
            parsed = urlparse(self.path)
            query_params = parse_qs(parsed.query)
            if query_params:
                payload = {
                    **{
                        key: values[0] if isinstance(values, list) and values else values
                        for key, values in query_params.items()
                    },
                    **payload
                }
            session_id = self._get_session_id()
            user_id = str(payload.get("userId", "")).strip()
            payload_session_id = str(payload.get("sessionId", "")).strip()
            current_page = str(payload.get("currentPage", "")).strip()
            if not session_id and payload_session_id:
                session_id = payload_session_id

            session_user = get_current_session_mongodb(session_id=session_id) if session_id else None
            resolved_user_id = user_id or str((session_user or {}).get("_id", "")).strip()

            if not session_id and not resolved_user_id:
                self._send_json({"ok": False, "message": "No active session to log out.", "sessionRemoved": False}, status=HTTPStatus.UNAUTHORIZED)
                return

            if current_page and resolved_user_id:
                update_user_current_page_mongodb(resolved_user_id, current_page)
            if session_id and current_page:
                update_session_current_page_mongodb(session_id, current_page)

            deleted_by_session = clear_user_session_mongodb(session_id)
            deleted_by_user = clear_user_sessions_for_user_mongodb(resolved_user_id)
            total_deleted = int(deleted_by_session or 0) + int(deleted_by_user or 0)
            session_still_exists = has_active_user_session_mongodb(user_id=resolved_user_id, session_id=session_id)

            if total_deleted <= 0 or session_still_exists:
                self._send_json({
                    "ok": False,
                    "message": "Logout failed because the session was not removed from login_status.",
                    "clearedSessionId": session_id,
                    "clearedUserId": resolved_user_id,
                    "deletedBySession": deleted_by_session,
                    "deletedByUser": deleted_by_user,
                    "sessionStillExists": session_still_exists,
                    "sessionRemoved": False
                }, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._clear_session_cookie()
            self._send_json({
                "ok": True,
                "source": "mongodb",
                "clearedSessionId": session_id,
                "clearedUserId": resolved_user_id,
                "deletedBySession": deleted_by_session,
                "deletedByUser": deleted_by_user,
                "sessionStillExists": False,
                "sessionRemoved": True
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Logout failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_page_update(self):
        try:
            session_id = self._get_session_id()

            payload = self._read_request_payload()
            current_page = str(payload.get("currentPage", "")).strip()
            user_id = str(payload.get("userId", "")).strip()
            payload_session_id = str(payload.get("sessionId", "")).strip()
            if not session_id and payload_session_id:
                session_id = payload_session_id

            if not current_page:
                self._send_json({"ok": False, "message": "Current page is required."}, status=HTTPStatus.BAD_REQUEST)
                return

            saved_page = None
            if session_id:
                saved_page = update_session_current_page_mongodb(session_id, current_page)
            if not saved_page and user_id:
                saved_page = update_user_current_page_mongodb(user_id, current_page)
            if not saved_page:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({"ok": True, "source": "mongodb", "currentPage": saved_page})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Page tracking failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_session_ping(self):
        try:
            session_id = self._get_session_id()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            payload = self._read_request_payload()
            current_page = str(payload.get("currentPage", "")).strip() or None

            session = heartbeat_user_session_mongodb(session_id, current_page=current_page)
            if not session:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({
                "ok": True,
                "source": "mongodb",
                "currentPage": session.get("current_page"),
                "lastSeenAt": session.get("last_seen_at"),
                "expiresAt": session.get("expires_at")
            })
        except Exception as error:
            self._send_json({"ok": False, "message": f"Session heartbeat failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_next_member_id(self):
        try:
            member_id = get_next_member_id_mongodb()
            self._send_json({"ok": True, "source": "mongodb", "memberId": member_id})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Next member lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_member_search(self):
        try:
            session_id = self._get_session_id()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            parsed = urlparse(self.path)
            query = parse_qs(parsed.query).get("q", [""])[0]
            members = search_members_mongodb(session_id=session_id, query=query)
            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "members",
                "members": members
            })
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Member search failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_member_details(self):
        try:
            parsed = urlparse(self.path)
            query_params = parse_qs(parsed.query)
            session_id = self._get_session_id() or query_params.get("sessionId", [""])[0]
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            member_id = query_params.get("memberId", [""])[0]
            member = get_member_details_for_librarian_mongodb(session_id=session_id, member_id=member_id)
            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "members",
                "member": member
            })
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Member details failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_register(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))

            name = str(payload.get("name", "")).strip()
            email = str(payload.get("email", "")).strip()
            password = str(payload.get("password", "")).strip()

            if not name or not email or not password:
                self._send_json({"ok": False, "message": "Name, email, and password are required."}, status=HTTPStatus.BAD_REQUEST)
                return

            user = register_member_mongodb(name, email, password)
            session_record = create_user_session_mongodb(user)
            self._set_session_cookie(session_record["session_id"])
            self._send_json({"ok": True, "source": "mongodb", "user": user})
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Registration failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_fee_payment(self):
        try:
            payload = self._read_request_payload()
            session_id = self._get_session_id() or str(payload.get("sessionId", "")).strip()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            member_id = str(payload.get("memberId", "")).strip()
            payment_method = str(payload.get("paymentMethod", "")).strip() or "counter_cash"
            notes = str(payload.get("notes", "")).strip()
            amount_paid = payload.get("amountPaid", 0)

            payment_result = record_fee_payment_mongodb(
                session_id=session_id,
                member_id=member_id,
                amount_paid=amount_paid,
                payment_method=payment_method,
                notes=notes
            )
            self._send_json({
                "ok": True,
                "source": "mongodb",
                "collection": "fee_payments",
                "payment": payment_result.get("payment"),
                "member": payment_result.get("member"),
                "previousOutstandingBalance": payment_result.get("previous_outstanding_balance"),
                "newOutstandingBalance": payment_result.get("new_outstanding_balance")
            })
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Fee payment failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_loan_request(self):
        try:
            session_id = self._get_session_id()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            book_id = str(payload.get("bookId", "")).strip()
            copy_no = str(payload.get("copyNo", "")).strip()

            if not book_id or not copy_no:
                self._send_json({"ok": False, "message": "Book ID and copy number are required."}, status=HTTPStatus.BAD_REQUEST)
                return

            request_doc = create_loan_request_mongodb(session_id, book_id, copy_no)
            self._send_json({"ok": True, "source": "mongodb", "request": request_doc})
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Loan request failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_return_book(self):
        try:
            session_id = self._get_session_id()
            session = get_current_session_mongodb(session_id=session_id)
            if not session:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            profile = get_current_profile_document_mongodb(session_id=session_id)
            if not profile or profile.get("role") != "librarian":
                self._send_json({"ok": False, "message": "Only librarians can return books."}, status=HTTPStatus.FORBIDDEN)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            copy_no = str(payload.get("copyNo", "")).strip()

            if not copy_no:
                self._send_json({"ok": False, "message": "Copy number is required."}, status=HTTPStatus.BAD_REQUEST)
                return

            result = return_book(copy_no)
            if result != "Book returned successfully":
                self._send_json({"ok": False, "message": result}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"ok": True, "source": "mongodb", "message": result})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Return book failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_issue_book_request(self):
        try:
            session_id = self._get_session_id()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            request_id = str(payload.get("requestId", "")).strip()

            if not request_id:
                self._send_json({"ok": False, "message": "Request ID is required."}, status=HTTPStatus.BAD_REQUEST)
                return

            issued = issue_loan_request_mongodb(session_id, request_id)
            self._send_json({"ok": True, "source": "mongodb", "issued": issued})
        except ValueError as error:
            self._send_json({"ok": False, "message": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"ok": False, "message": f"Issue request failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host="0.0.0.0", port=int(os.getenv("PORT", "10000"))):
    global _shutdown_started

    def cleanup_active_sessions():
        global _shutdown_started
        if _shutdown_started:
            return

        _shutdown_started = True
        try:
            clear_all_user_sessions_mongodb()
            print("Cleared active login_status records during server shutdown.")
        except Exception as error:
            print(f"Failed to clear login_status during shutdown: {error}")

    server = ThreadingHTTPServer((host, port), LibraryRequestHandler)
    atexit.register(cleanup_active_sessions)

    def handle_shutdown_signal(signum, frame):
        cleanup_active_sessions()
        server.shutdown()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        shutdown_signal = getattr(signal, signal_name, None)
        if shutdown_signal is None:
            continue
        try:
            signal.signal(shutdown_signal, handle_shutdown_signal)
        except (ValueError, OSError):
            continue

    print(f"Serving Library app on {host}:{port}")

    warmup_attempts = 3
    warmup_delay_seconds = 3
    warmup_succeeded = False

    for attempt in range(1, warmup_attempts + 1):
        try:
            warm_mongodb_connection()
            print(f"MongoDB connection warm-up succeeded on attempt {attempt}.")
            warmup_succeeded = True
            break
        except Exception as error:
            print(f"MongoDB connection warm-up failed on attempt {attempt} of {warmup_attempts}: {error}")
            if attempt < warmup_attempts:
                print(f"Retrying MongoDB warm-up in {warmup_delay_seconds} seconds...")
                time.sleep(warmup_delay_seconds)

    if not warmup_succeeded:
        print("Server is already running; MongoDB-backed routes may report API errors until connectivity recovers.")
    try:
        server.serve_forever()
    finally:
        cleanup_active_sessions()
        server.server_close()


if __name__ == "__main__":
    run_server(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )
