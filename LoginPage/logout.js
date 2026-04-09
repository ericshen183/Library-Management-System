import { confirmLogout, getSessionMember, redirectToLoginPage, reportCurrentPage, syncTrackedSession } from "./JS_members.js";

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const cachedUser = getSessionMember();

    if (cachedUser) {
        setText("logoutMessage", `Logging out ${cachedUser.name || cachedUser._id || "the current user"}...`);
    } else {
        setText("logoutMessage", "Removing the active session and returning to the login page...");
    }

    try {
        await syncTrackedSession();
        await reportCurrentPage("/LoginPage/logout.html");
        const syncedUser = getSessionMember();
        if (syncedUser) {
            setText("logoutMessage", `Logging out ${syncedUser.name || syncedUser._id || "the current user"}...`);
        }
    } catch (error) {
        if (String(error.message || "").toLowerCase().includes("no active session")) {
            setText("logoutStatus", "Session already expired. Redirecting to login page...");
            setTimeout(() => {
                redirectToLoginPage("", { showAlert: false });
            }, 600);
            return;
        }
        setText("logoutStatus", `Session lookup failed: ${error.message}`);
    }

    setText("logoutStatus", "Removing active session from login_status...");
    const loggedOut = await confirmLogout("/LoginPage/Login.html");
    if (loggedOut) {
        setText("logoutStatus", "Session removed. Redirecting to login page...");
        setTimeout(() => {
            window.location.href = "/LoginPage/Login.html";
        }, 1200);
    } else {
        setText("logoutStatus", "Logout failed. The active session was not removed.");
    }
});
