import { clearSession, getSessionMember, normalizeLoanEntries, reportCurrentPage, saveSessionUser, syncTrackedSession } from "./JS_members.js";

function renderDashboard(member) {
    const roleLabel = member.role ? member.role.charAt(0).toUpperCase() + member.role.slice(1) : 'User';
    const currentLoans = normalizeLoanEntries(member.current_loans);
    const loanHistory = normalizeLoanEntries(member.loan_history);
    $('#userNameDisplay').text(member.name);
    $('#userEmailDisplay').text(member.email);
    $('#userIdDisplay').text(member._id);
    $('#userRoleDisplay').text(roleLabel);
    $('#userPhoneDisplay').text(member.phone || 'Not Provided');
    $('#userLoansDisplay').text(currentLoans.length);
    $('#userHistoryDisplay').text(loanHistory.length);
    $('#userFeeDisplay').text(`$${Number(member.late_fee || 0).toFixed(2)}`);
    $('#roleDescription').text(`${roleLabel} account connected to the live MongoDB-backed session.`);
    $('#accountStatusMessage').text('Live account data loaded from MongoDB.');
}

async function loadAccountFromServer() {
    const response = await fetch(`/api/account?t=${Date.now()}`);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== 'mongodb') {
        throw new Error(result.message || 'Unable to load account data.');
    }

    if (!result.collection || (result.collection !== 'members' && result.collection !== 'librarians')) {
        throw new Error('Account data did not come from a profile collection.');
    }

    saveSessionUser({
        user_id: result.user._id,
        login_id: result.user._id,
        role: result.user.role,
        name: result.user.name,
        email: result.user.email,
        profile: result.user
    });

    return result.user;
}

$(document).ready(async function() {
    const cachedMember = getSessionMember();

    if (cachedMember) {
        renderDashboard(cachedMember);
        $('#accountStatusMessage').text('Showing the tracked signed-in user while MongoDB refreshes.');
    }

    try {
        await syncTrackedSession();
        await reportCurrentPage("/LoginPage/dashboard.html");
        const member = await loadAccountFromServer();
        renderDashboard(member);
        $('#accountStatusMessage').text(`Live account data loaded from MongoDB (Library.${member.role === 'librarian' ? 'librarians' : 'members'}).`);
    } catch (error) {
        console.error("Error loading account from server: ", error);
        if (cachedMember) {
            $('#accountStatusMessage').text(`Showing tracked user. MongoDB refresh failed: ${error.message}`);
            return;
        }

        $('#accountStatusMessage').text(`Account data could not be loaded from MongoDB: ${error.message}`);
        $('#userNameDisplay').text('Account Load Failed');
        $('#roleDescription').text('The dashboard could not fetch the current session user from the database.');
        alert("You are not logged in. Redirecting to login page...");
        window.location.href = "/LoginPage/Login.html";
        return;
    }

    $('#logoutBtn').on('click', async function(event) {
        event.preventDefault();
        clearSession();
        try {
            await fetch('/api/logout', {
                method: 'POST'
            });
        } catch (error) {
            console.error("Error logging out: ", error);
        }
        alert("You have been successfully logged out.");
        window.location.href = "/LoginPage/Login.html";
    });
});
