import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from library import (
    authenticate_user_mongodb,
    clear_user_session_mongodb,
    create_user_session_mongodb,
    get_books_mongodb,
    get_current_loans_mongodb,
    get_current_profile_document_mongodb,
    get_current_session_mongodb,
    get_next_member_id_mongodb,
    get_library_page_data_mongodb,
    register_member_mongodb,
    update_login_status_mongodb,
    update_session_current_page_mongodb,
    warm_mongodb_connection,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR
SESSION_COOKIE_NAME = "library_session_id"


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

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_GET(self):
        request_path = urlparse(self.path).path

        if request_path == "/api/session":
            self._handle_session()
            return

        if request_path == "/api/account":
            self._handle_account()
            return

        if request_path == "/api/books":
            self._handle_books()
            return

        if request_path == "/api/library-data":
            self._handle_library_data()
            return

        if request_path == "/api/loans":
            self._handle_loans()
            return

        if request_path == "/api/next-member-id":
            self._handle_next_member_id()
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

            self._send_json({"ok": True, "source": "mongodb", "user": user, "currentPage": user.get("current_page")})
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
            self._send_json({"ok": True, "source": "mongodb", "books": books})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Book lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

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

    def _handle_logout(self):
        try:
            clear_user_session_mongodb(self._get_session_id())
            self._clear_session_cookie()
            self._send_json({"ok": True})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Logout failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_page_update(self):
        try:
            session_id = self._get_session_id()
            if not session_id:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            current_page = str(payload.get("currentPage", "")).strip()

            if not current_page:
                self._send_json({"ok": False, "message": "Current page is required."}, status=HTTPStatus.BAD_REQUEST)
                return

            saved_page = update_session_current_page_mongodb(session_id, current_page)
            if not saved_page:
                self._send_json({"ok": False, "message": "No active session."}, status=HTTPStatus.UNAUTHORIZED)
                return

            self._send_json({"ok": True, "source": "mongodb", "currentPage": saved_page})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Page tracking failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_next_member_id(self):
        try:
            member_id = get_next_member_id_mongodb()
            self._send_json({"ok": True, "source": "mongodb", "memberId": member_id})
        except Exception as error:
            self._send_json({"ok": False, "message": f"Next member lookup failed: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

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

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host="127.0.0.1", port=8000):
    try:
        warm_mongodb_connection()
        print("MongoDB connection warm-up succeeded.")
    except Exception as error:
        print(f"MongoDB connection warm-up failed: {error}")
        print("Starting the local server anyway so the app can load and report API errors.")
    server = ThreadingHTTPServer((host, port), LibraryRequestHandler)
    print(f"Serving Library app at http://{host}:{port}/LoginPage/Login.html")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
