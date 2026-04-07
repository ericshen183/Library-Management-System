const MEMBER_SESSION_KEY = "session_member";
const MEMBER_PERSIST_KEY = "library_current_user";

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
        late_fee: profile.late_fee || 0,
        phone: profile.phone || null
    };

    writeStoredSession(sessionUser);
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
    sessionStorage.removeItem(MEMBER_SESSION_KEY);
    sessionStorage.removeItem("session_userid");
    sessionStorage.removeItem("session_username");
    sessionStorage.removeItem("session_role");
    localStorage.removeItem(MEMBER_PERSIST_KEY);
    localStorage.removeItem("session_userid");
    localStorage.removeItem("session_username");
    localStorage.removeItem("session_role");
}

async function syncTrackedSession(fetchUrl = `/api/session?t=${Date.now()}`) {
    const response = await fetch(fetchUrl);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== "mongodb" || !result.user) {
        throw new Error(result.message || "No active session.");
    }

    saveSessionUser({
        user_id: result.user._id,
        login_id: result.user._id,
        role: result.user.role,
        name: result.user.name,
        email: result.user.email,
        profile: result.user
    });

    return getSessionMember();
}

async function reportCurrentPage(currentPage) {
    if (!currentPage) {
        return;
    }

    try {
        await fetch("/api/session/page", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ currentPage })
        });
    } catch (error) {
        console.error("Unable to report current page:", error);
    }
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
    getSessionMember,
    normalizeLoanEntries,
    saveSession,
    saveSessionUser,
    syncTrackedSession,
    reportCurrentPage
};
