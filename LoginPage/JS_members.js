const MEMBER_SESSION_KEY = "session_member";
const MEMBER_PERSIST_KEY = "library_current_user";
const SELECTED_MEMBER_KEY = "library_selected_member";
const SELECTED_MEMBER_ID_KEY = "library_selected_member_id";
const SESSION_LAST_ACTIVITY_KEY = "library_last_activity_at";
const SESSION_ID_KEY = "library_session_id";
const SESSION_TIMEOUT_MINUTES = 10;
const SESSION_TIMEOUT_MS = SESSION_TIMEOUT_MINUTES * 60 * 1000;
const SESSION_HEARTBEAT_MS = 60000;
const SESSION_ACTIVITY_EVENTS = ["click", "keydown", "mousedown", "mousemove", "scroll", "touchstart"];
let sessionHeartbeatId = null;
let sessionExpiryTimeoutId = null;
let sessionActivityListenersBound = false;
let sessionExpiryHandled = false;
let sessionPageTrackingBound = false;

function buildJsonBlob(payload) {
    return new Blob([JSON.stringify(payload)], { type: "application/json" });
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

async function postJsonWithKeepalive(url, payload, { useBeaconFallback = false } = {}) {
    try {
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload),
            credentials: "same-origin",
            cache: "no-store",
            keepalive: true
        });

        return response;
    } catch (error) {
        if (useBeaconFallback && navigator.sendBeacon) {
            const queued = navigator.sendBeacon(url, buildJsonBlob(payload));
            if (queued) {
                return null;
            }
        }

        throw error;
    }
}

function getSessionTimeoutLabel() {
    return `${SESSION_TIMEOUT_MINUTES} minutes`;
}

function getLoansPagePath(member = getSessionMember()) {
    return String(member && member.role || "").trim().toLowerCase() === "librarian"
        ? "/Loans/librarian_loans.html"
        : "/Loans/loans.html";
}

function markSessionActivity() {
    const timestamp = String(Date.now());
    sessionStorage.setItem(SESSION_LAST_ACTIVITY_KEY, timestamp);
    localStorage.setItem(SESSION_LAST_ACTIVITY_KEY, timestamp);
    scheduleSessionExpiryTimer();
}

function getLastSessionActivity() {
    const storedTimestamp = sessionStorage.getItem(SESSION_LAST_ACTIVITY_KEY) || localStorage.getItem(SESSION_LAST_ACTIVITY_KEY);
    const parsedTimestamp = Number(storedTimestamp);
    return Number.isFinite(parsedTimestamp) && parsedTimestamp > 0 ? parsedTimestamp : Date.now();
}

function bindSessionActivityListeners() {
    if (sessionActivityListenersBound) {
        return;
    }

    SESSION_ACTIVITY_EVENTS.forEach((eventName) => {
        window.addEventListener(eventName, markSessionActivity, { passive: true });
    });
    sessionActivityListenersBound = true;
}

function isMissingSessionErrorMessage(message) {
    const normalizedMessage = String(message || "").trim().toLowerCase();
    return normalizedMessage.includes("no active session")
        || normalizedMessage.includes("session lookup failed")
        || normalizedMessage.includes("session was not removed")
        || normalizedMessage.includes("session closed after");
}

function redirectToLoginPage(message = "", { showAlert = true } = {}) {
    if (sessionExpiryHandled) {
        return;
    }

    sessionExpiryHandled = true;
    stopSessionHeartbeat();
    clearSession();
    if (showAlert && message) {
        alert(message);
    }
    window.location.href = "/LoginPage/Login.html";
}

function handleSessionClosed(message = `Session closed after ${getSessionTimeoutLabel()} of inactivity.`) {
    redirectToLoginPage(message, { showAlert: true });
}

function writeStoredSession(sessionUser) {
    const serializedUser = JSON.stringify(sessionUser);
    sessionStorage.setItem(MEMBER_SESSION_KEY, serializedUser);
    localStorage.setItem(MEMBER_PERSIST_KEY, serializedUser);
    sessionStorage.setItem("session_userid", sessionUser._id || "");
    sessionStorage.setItem("session_username", sessionUser.email || "");
    sessionStorage.setItem("session_role", sessionUser.role || "");
    localStorage.setItem("session_userid", sessionUser._id || "");
    localStorage.setItem("session_username", sessionUser.email || "");
    localStorage.setItem("session_role", sessionUser.role || "");
    if (sessionUser.session_id) {
        sessionStorage.setItem(SESSION_ID_KEY, sessionUser.session_id);
        localStorage.setItem(SESSION_ID_KEY, sessionUser.session_id);
    }
    document.documentElement.dataset.sessionId = sessionUser.session_id || "";
    document.documentElement.dataset.userId = sessionUser._id || "";
    document.documentElement.dataset.currentPage = window.location.pathname;
}

function saveSession(member) {
    writeStoredSession(member);
}

function saveSessionUser(user) {
    const profile = user.profile || {};
    const currentLoans = normalizeLoanEntries(profile.current_loans);
    const loanHistory = normalizeLoanEntries(profile.loan_history);
    const sessionUser = {
        _id: profile._id || user.user_id,
        name: user.name || profile.name,
        email: user.email || profile.email,
        role: user.role,
        current_loans: currentLoans,
        loan_history: loanHistory,
        payment_history: Array.isArray(profile.payment_history) ? profile.payment_history : [],
        late_fee: profile.late_fee || 0,
        outstanding_balance: profile.outstanding_balance ?? profile.late_fee ?? 0,
        total_overdue_charges: profile.total_overdue_charges || 0,
        total_payments: profile.total_payments || 0,
        phone: profile.phone || null,
        session_id: user.session_id || profile.session_id || getTrackedSessionId()
    };

    writeStoredSession(sessionUser);
    markSessionActivity();
}

function getSessionMember() {
    const storedMember = sessionStorage.getItem(MEMBER_SESSION_KEY);
    if (storedMember) {
        return JSON.parse(storedMember);
    }

    const persistedMember = localStorage.getItem(MEMBER_PERSIST_KEY);
    if (!persistedMember) {
        return null;
    }

    const parsedMember = JSON.parse(persistedMember);
    writeStoredSession(parsedMember);
    return parsedMember;
}

function clearSession() {
    stopSessionHeartbeat();
    sessionStorage.removeItem(MEMBER_SESSION_KEY);
    sessionStorage.removeItem("session_userid");
    sessionStorage.removeItem("session_username");
    sessionStorage.removeItem("session_role");
    sessionStorage.removeItem(SESSION_ID_KEY);
    sessionStorage.removeItem(SESSION_LAST_ACTIVITY_KEY);
    localStorage.removeItem(MEMBER_PERSIST_KEY);
    localStorage.removeItem("session_userid");
    localStorage.removeItem("session_username");
    localStorage.removeItem("session_role");
    localStorage.removeItem(SESSION_ID_KEY);
    localStorage.removeItem(SESSION_LAST_ACTIVITY_KEY);
    document.documentElement.dataset.sessionId = "";
    document.documentElement.dataset.userId = "";
    document.documentElement.dataset.currentPage = "";
}

function scheduleSessionExpiryTimer() {
    if (sessionExpiryTimeoutId) {
        window.clearTimeout(sessionExpiryTimeoutId);
    }

    const msUntilExpiry = Math.max(0, SESSION_TIMEOUT_MS - (Date.now() - getLastSessionActivity()));
    sessionExpiryTimeoutId = window.setTimeout(() => {
        handleSessionClosed(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`);
    }, msUntilExpiry);
}

function getTrackedUserId() {
    const currentUser = getSessionMember();
    return currentUser && currentUser._id ? currentUser._id : "";
}

function getTrackedSessionId() {
    return sessionStorage.getItem(SESSION_ID_KEY) || localStorage.getItem(SESSION_ID_KEY) || "";
}

function bindCurrentPageTracking(currentPage) {
    if (sessionPageTrackingBound || !currentPage) {
        return;
    }

    const sendCurrentPageBeacon = (pagePath) => {
        if (!pagePath || !navigator.sendBeacon) {
            return;
        }
        navigator.sendBeacon("/api/session/page", buildJsonBlob({
            currentPage: pagePath,
            userId: getTrackedUserId(),
            sessionId: getTrackedSessionId()
        }));
    };

    document.addEventListener("click", (event) => {
        const anchor = event.target.closest("a[href]");
        if (!anchor) {
            return;
        }
        if (anchor.id === "logoutBtn") {
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
                .catch((error) => console.error("Unable to track navigation target:", error))
                .finally(() => {
                    window.location.href = `${targetUrl.pathname}${targetUrl.search}${targetUrl.hash}`;
                });
        } catch (error) {
            console.error("Unable to track navigation target:", error);
        }
    }, true);

    window.addEventListener("pagehide", () => {
        sendCurrentPageBeacon(window.location.pathname);
    });

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") {
            sendCurrentPageBeacon(window.location.pathname);
        }
    });

    sessionPageTrackingBound = true;
}

async function performLogout(redirectPath = "/LoginPage/Login.html") {
    stopSessionHeartbeat();
    await reportCurrentPage("/LoginPage/logout.html");
    window.location.href = "/LoginPage/logout.html";
    return true;
}

async function confirmLogout(redirectPath = "/LoginPage/Login.html") {
    const currentUser = getSessionMember();
    const payload = {
        userId: currentUser && currentUser._id,
        sessionId: getTrackedSessionId(),
        currentPage: "/LoginPage/Login.html"
    };

    try {
        const response = await postJsonWithKeepalive("/api/logout", payload);
        if (!response) {
            throw new Error("Logout request did not reach the server.");
        }

        const result = await response.json();
        console.log("[LOGOUT] Server logout response", {
            ok: response.ok,
            status: response.status,
            result
        });

        if (!response.ok || !result || !result.ok || !result.sessionRemoved) {
            if (isMissingSessionErrorMessage(result && result.message) || (result && result.sessionStillExists === false)) {
                clearSession();
                return true;
            }
            throw new Error((result && result.message) || "Logout failed because the session was not removed from login_status.");
        }

        clearSession();
        return true;
    } catch (error) {
        console.error("Error logging out:", error);
        try {
            const fallbackResponse = await fetch(buildLogoutUrl(payload), {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                keepalive: true
            });
            const fallbackResult = await fallbackResponse.json();
            if (!fallbackResponse.ok || !fallbackResult.ok || !fallbackResult.sessionRemoved) {
                if (isMissingSessionErrorMessage(fallbackResult && fallbackResult.message) || (fallbackResult && fallbackResult.sessionStillExists === false)) {
                    clearSession();
                    return true;
                }
                throw new Error((fallbackResult && fallbackResult.message) || "Logout fallback failed.");
            }

            clearSession();
            return true;
        } catch (fallbackError) {
            console.error("Logout fallback failed:", fallbackError);
            alert(fallbackError.message || error.message || "Logout failed. Your session is still active.");
            return false;
        }
    }
}

function saveSelectedMember(member) {
    if (!member || !member._id) {
        return;
    }

    const serializedMember = JSON.stringify(member);
    sessionStorage.setItem(SELECTED_MEMBER_KEY, serializedMember);
    localStorage.setItem(SELECTED_MEMBER_KEY, serializedMember);
    sessionStorage.setItem(SELECTED_MEMBER_ID_KEY, member._id);
    localStorage.setItem(SELECTED_MEMBER_ID_KEY, member._id);
}

function getSelectedMember() {
    const storedMember = sessionStorage.getItem(SELECTED_MEMBER_KEY) || localStorage.getItem(SELECTED_MEMBER_KEY);
    if (!storedMember) {
        return null;
    }

    try {
        const parsedMember = JSON.parse(storedMember);
        saveSelectedMember(parsedMember);
        return parsedMember;
    } catch (error) {
        console.error("Unable to parse selected member:", error);
        return null;
    }
}

function getSelectedMemberId() {
    return sessionStorage.getItem(SELECTED_MEMBER_ID_KEY) || localStorage.getItem(SELECTED_MEMBER_ID_KEY) || "";
}

function clearSelectedMember() {
    sessionStorage.removeItem(SELECTED_MEMBER_KEY);
    localStorage.removeItem(SELECTED_MEMBER_KEY);
    sessionStorage.removeItem(SELECTED_MEMBER_ID_KEY);
    localStorage.removeItem(SELECTED_MEMBER_ID_KEY);
}

async function syncTrackedSession(fetchUrl = `/api/session?t=${Date.now()}`) {
    const response = await fetch(fetchUrl);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== "mongodb" || !result.user) {
        if (isMissingSessionErrorMessage(result && result.message)) {
            redirectToLoginPage(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`);
        }
        throw new Error(result.message || "No active session.");
    }

    saveSessionUser({
        user_id: result.user._id,
        login_id: result.user._id,
        role: result.user.role,
        name: result.user.name,
        email: result.user.email,
        profile: result.user,
        session_id: result.sessionId
    });
    markSessionActivity();

    return getSessionMember();
}

async function reportCurrentPage(currentPage) {
    if (!currentPage) {
        return;
    }

    try {
        const response = await postJsonWithKeepalive("/api/session/page", {
            currentPage,
            userId: getTrackedUserId(),
            sessionId: getTrackedSessionId()
        }, { useBeaconFallback: true });
        if (!response) {
            return;
        }

        const result = await response.json();
        if (!response.ok || !result.ok) {
            console.error("Unable to report current page:", result.message || "Request failed");
        }
        bindCurrentPageTracking(currentPage);
    } catch (error) {
        console.error("Unable to report current page:", error);
    }
}

function stopSessionHeartbeat() {
    if (sessionHeartbeatId) {
        window.clearInterval(sessionHeartbeatId);
        sessionHeartbeatId = null;
    }
    if (sessionExpiryTimeoutId) {
        window.clearTimeout(sessionExpiryTimeoutId);
        sessionExpiryTimeoutId = null;
    }
}

function startSessionHeartbeat(currentPage) {
    stopSessionHeartbeat();
    sessionExpiryHandled = false;
    bindSessionActivityListeners();
    markSessionActivity();
    scheduleSessionExpiryTimer();

    const sendHeartbeat = async () => {
        if (Date.now() - getLastSessionActivity() >= SESSION_TIMEOUT_MS) {
            handleSessionClosed(`Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`);
            return;
        }

        try {
            const response = await postJsonWithKeepalive("/api/session/ping", { currentPage });
            const result = await response.json();

            if (!response.ok || !result.ok) {
                handleSessionClosed(result.message || `Session closed after ${getSessionTimeoutLabel()} of inactivity. Please sign in again.`);
                return;
            }
            scheduleSessionExpiryTimer();
        } catch (error) {
            console.error("Unable to send session heartbeat:", error);
        }
    };

    sendHeartbeat();
    sessionHeartbeatId = window.setInterval(sendHeartbeat, SESSION_HEARTBEAT_MS);
}

function normalizeLoanEntries(entries) {
    if (!entries) {
        return [];
    }

    if (Array.isArray(entries)) {
        return entries;
    }

    if (typeof entries === "object") {
        if (entries.copy_no || entries.loan_id) {
            return [entries];
        }

        return Object.keys(entries)
            .sort((left, right) => Number(left) - Number(right))
            .map((key) => entries[key])
            .filter((entry) => entry && typeof entry === "object");
    }

    return [];
}

function findMemberByCredentials(members, memberId, email) {
    return members.find((member) =>
        member._id.toLowerCase() === memberId.toLowerCase() &&
        member.email.toLowerCase() === email.toLowerCase()
    );
}

export {
    clearSession,
    clearSelectedMember,
    confirmLogout,
    getSelectedMember,
    getSelectedMemberId,
    getLoansPagePath,
    getSessionMember,
    getSessionTimeoutLabel,
    getTrackedSessionId,
    normalizeLoanEntries,
    performLogout,
    saveSession,
    saveSelectedMember,
    saveSessionUser,
    syncTrackedSession,
    reportCurrentPage,
    redirectToLoginPage,
    startSessionHeartbeat,
    stopSessionHeartbeat
};
