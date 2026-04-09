const SESSION_TIMEOUT_MINUTES = 10;
const SESSION_TIMEOUT_MS = SESSION_TIMEOUT_MINUTES * 60 * 1000;
const SESSION_HEARTBEAT_MS = 60000;
let sessionHeartbeatId = null;
let sessionExpiryTimeoutId = null;
let navigationTrackingBound = false;

function getSessionTimeoutLabel() {
    return `${SESSION_TIMEOUT_MINUTES} minutes`;
}

function getStoredSessionMember() {
    const storedMember = sessionStorage.getItem("session_member") || localStorage.getItem("library_current_user");
    if (!storedMember) {
        return null;
    }

    try {
        return JSON.parse(storedMember);
    } catch (error) {
        console.error("[LIBRARIAN LOANS] Failed to parse stored session member", error);
        return null;
    }
}

function getTrackedSessionId() {
    return sessionStorage.getItem("library_session_id") || localStorage.getItem("library_session_id") || "";
}

function clearStoredSession() {
    sessionStorage.removeItem("session_member");
    sessionStorage.removeItem("session_userid");
    sessionStorage.removeItem("session_username");
    sessionStorage.removeItem("session_role");
    sessionStorage.removeItem("library_session_id");
    sessionStorage.removeItem("library_last_activity_at");
    localStorage.removeItem("library_current_user");
    localStorage.removeItem("session_userid");
    localStorage.removeItem("session_username");
    localStorage.removeItem("session_role");
    localStorage.removeItem("library_session_id");
    localStorage.removeItem("library_last_activity_at");
}

function markSessionActivity() {
    const timestamp = String(Date.now());
    sessionStorage.setItem("library_last_activity_at", timestamp);
    localStorage.setItem("library_last_activity_at", timestamp);
    scheduleSessionExpiryTimer();
}

function getLastSessionActivity() {
    const storedTimestamp = sessionStorage.getItem("library_last_activity_at") || localStorage.getItem("library_last_activity_at");
    const parsedTimestamp = Number(storedTimestamp);
    return Number.isFinite(parsedTimestamp) && parsedTimestamp > 0 ? parsedTimestamp : Date.now();
}

function scheduleSessionExpiryTimer() {
    if (sessionExpiryTimeoutId) {
        window.clearTimeout(sessionExpiryTimeoutId);
    }

    const msUntilExpiry = Math.max(0, SESSION_TIMEOUT_MS - (Date.now() - getLastSessionActivity()));
    sessionExpiryTimeoutId = window.setTimeout(() => {
        redirectToLoginPage(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`, true);
    }, msUntilExpiry);
}

function redirectToLoginPage(message = "", showAlert = false) {
    if (sessionHeartbeatId) {
        window.clearInterval(sessionHeartbeatId);
        sessionHeartbeatId = null;
    }
    if (sessionExpiryTimeoutId) {
        window.clearTimeout(sessionExpiryTimeoutId);
        sessionExpiryTimeoutId = null;
    }
    clearStoredSession();
    if (showAlert && message) {
        alert(message);
    }
    window.location.href = "/LoginPage/Login.html";
}

function isLibrarianRole(role) {
    return String(role || "").trim().toLowerCase() === "librarian";
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

function renderPlaceholder(message) {
    const container = document.getElementById("librarianLoanOverview");
    if (!container) {
        return;
    }

    container.innerHTML = `<div class="loan-empty">${message}</div>`;
}

function buildLogoutUrl(payload) {
    const params = new URLSearchParams();
    if (payload && payload.userId) {
        params.set("userId", payload.userId);
    }
    if (payload && payload.currentPage) {
        params.set("currentPage", payload.currentPage);
    }
    params.set("_", String(Date.now()));
    return `/api/logout?${params.toString()}`;
}

function renderLoanRequests(requests) {
    const container = document.getElementById("librarianLoanOverview");
    if (!container) {
        return;
    }

    if (!Array.isArray(requests) || !requests.length) {
        container.innerHTML = '<div class="loan-empty">No loan request documents were returned from Library.loan_requests.</div>';
        return;
    }

    container.innerHTML = requests.map((request) => {
        const isPending = String(request.status || "").trim().toLowerCase() === "pending";
        const buttonMarkup = isPending
            ? `<button class="issue-request-btn" data-request-id="${request._id || request.request_id || ""}">Issue</button>`
            : '<button class="issue-request-btn" type="button" disabled>Not Pending</button>';

        return `
            <article class="loan-item">
                <div class="loan-title">${request.book_title || "Unknown Book"}</div>
                <div class="loan-meta">
                    <div><strong>Request ID:</strong> ${request._id || request.request_id || "Unknown"}</div>
                    <div><strong>Status:</strong> ${request.status || "unknown"}</div>
                    <div><strong>Member:</strong> ${request.member_name || "Unknown Member"} (${request.member_id || "Unknown"})</div>
                    <div><strong>Email:</strong> ${request.member_email || "Unknown"}</div>
                    <div><strong>Book ID:</strong> ${request.book_id || "Unknown"}</div>
                    <div><strong>Copy:</strong> ${request.copy_no || "Unknown"}</div>
                    <div><strong>Requested:</strong> ${request.request_date || "Unknown"}</div>
                    ${buttonMarkup}
                </div>
            </article>
        `;
    }).join("");
}

async function postJson(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(payload),
        credentials: "same-origin",
        cache: "no-store"
    });
}

async function reportCurrentPage(currentPage) {
    try {
        const currentUser = getStoredSessionMember();
        await postJson("/api/session/page", {
            currentPage,
            userId: currentUser && currentUser._id,
            sessionId: getTrackedSessionId()
        });
    } catch (error) {
        console.warn("[LIBRARIAN LOANS] Unable to report current page", error);
    }
}

function startSessionHeartbeat(currentPage) {
    if (sessionHeartbeatId) {
        window.clearInterval(sessionHeartbeatId);
    }
    markSessionActivity();
    scheduleSessionExpiryTimer();

    const sendHeartbeat = async () => {
        if (Date.now() - getLastSessionActivity() >= SESSION_TIMEOUT_MS) {
            redirectToLoginPage(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`, true);
            return;
        }

        try {
            const response = await postJson("/api/session/ping", { currentPage });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                redirectToLoginPage(result.message || `Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`, true);
                return;
            }
            scheduleSessionExpiryTimer();
        } catch (error) {
            console.warn("[LIBRARIAN LOANS] Heartbeat failed", error);
        }
    };

    sendHeartbeat();
    sessionHeartbeatId = window.setInterval(sendHeartbeat, SESSION_HEARTBEAT_MS);
}

function bindTrackedNavigation() {
    if (navigationTrackingBound) {
        return;
    }

    document.addEventListener("click", (event) => {
        const anchor = event.target.closest("a[href]");
        if (!anchor || anchor.id === "logoutBtn") {
            return;
        }

        const href = anchor.getAttribute("href") || "";
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) {
            return;
        }

        try {
            const targetUrl = new URL(href, window.location.origin);
            if (targetUrl.origin !== window.location.origin) {
                return;
            }

            event.preventDefault();
            reportCurrentPage(targetUrl.pathname)
                .catch((error) => console.error("[LIBRARIAN LOANS] Navigation tracking failed", error))
                .finally(() => {
                    window.location.href = `${targetUrl.pathname}${targetUrl.search}${targetUrl.hash}`;
                });
        } catch (error) {
            console.error("[LIBRARIAN LOANS] Invalid navigation target", error);
        }
    }, true);

    navigationTrackingBound = true;
}

async function performLogout() {
    if (sessionHeartbeatId) {
        window.clearInterval(sessionHeartbeatId);
        sessionHeartbeatId = null;
    }
    await reportCurrentPage("/LoginPage/logout.html");
    window.location.href = "/LoginPage/logout.html";
    return true;
}

async function loadOverview() {
    renderPlaceholder("Loading pending loan requests...");

    const response = await fetch(`/api/librarian-loans?t=${Date.now()}`, {
        credentials: "same-origin",
        cache: "no-store"
    });
    const result = await response.json();

    console.log("[LIBRARIAN LOANS] /api/librarian-loans response", {
        ok: response.ok,
        status: response.status,
        result
    });

    if (!response.ok || !result.ok) {
        throw new Error(result.message || "Unable to load librarian overview.");
    }

    const loanRequests = Array.isArray(result.loan_requests) ? result.loan_requests : [];
    const pendingCount = Number(result.pendingCount || 0);
    const totalCount = Number(result.totalCount || loanRequests.length);
    const currentUser = result.user || getStoredSessionMember() || {};

    setText("loansMemberName", currentUser.name || currentUser._id || "Librarian");
    setText("currentLoanCount", String(totalCount));
    setText("loanHistoryCount", String(pendingCount));
    setText(
        "loansStatusMessage",
        `Live librarian overview loaded from MongoDB (Library.loan_requests): ${totalCount} request document(s), ${pendingCount} pending. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`
    );
    renderLoanRequests(loanRequests);
}

async function issueRequest(requestId) {
    const response = await postJson("/api/issue-book-request", { requestId });
    const result = await response.json();

    if (!response.ok || !result.ok) {
        throw new Error(result.message || "Unable to issue the requested book.");
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const currentUser = getStoredSessionMember();

    if (currentUser && !isLibrarianRole(currentUser.role)) {
        window.location.replace("/Loans/loans.html");
        return;
    }

    if (currentUser) {
        setText("loansMemberName", currentUser.name || currentUser._id || "Librarian");
        setText("loansStatusMessage", `Showing the tracked signed-in librarian while MongoDB refreshes. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
        markSessionActivity();
    }

    ["click", "keydown", "mousedown", "mousemove", "scroll", "touchstart"].forEach((eventName) => {
        window.addEventListener(eventName, markSessionActivity, { passive: true });
    });

    try {
        await reportCurrentPage("/Loans/librarian_loans.html");
        bindTrackedNavigation();
        startSessionHeartbeat("/Loans/librarian_loans.html");
        await loadOverview();
    } catch (error) {
        console.error("[LIBRARIAN LOANS] Load failed", error);
        if (String(error.message || "").toLowerCase().includes("no active session")) {
            redirectToLoginPage(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`, true);
            return;
        }
        setText("loansStatusMessage", `MongoDB request failed: ${error.message}`);
        renderPlaceholder(`Unable to load loan requests: ${error.message}`);
    }

    document.addEventListener("click", async (event) => {
        const issueButton = event.target.closest(".issue-request-btn");
        if (issueButton && !issueButton.disabled) {
            const requestId = String(issueButton.getAttribute("data-request-id") || "").trim();
            if (!requestId) {
                return;
            }

            issueButton.disabled = true;
            issueButton.textContent = "Issuing...";

            try {
                await issueRequest(requestId);
                await loadOverview();
            } catch (error) {
                console.error("[LIBRARIAN LOANS] Issue failed", error);
                issueButton.disabled = false;
                issueButton.textContent = "Issue";
                setText("loansStatusMessage", `Issue failed: ${error.message}`);
                alert(error.message);
            }
            return;
        }

        const logoutButton = event.target.closest("#logoutBtn");
        if (logoutButton) {
            event.preventDefault();
            await performLogout();
        }
    });
});
