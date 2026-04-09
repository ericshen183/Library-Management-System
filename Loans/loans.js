import { getSessionMember, getSessionTimeoutLabel, normalizeLoanEntries, performLogout, reportCurrentPage, saveSessionUser, startSessionHeartbeat, syncTrackedSession } from "../LoginPage/JS_members.js";

function createLoanItem(loan, isHistory) {
    const returnLine = isHistory && loan.return_date
        ? `<div><strong>Returned:</strong> ${loan.return_date}</div>`
        : "";
    const displayFineAmount = Number(loan.outstanding_fine_amount ?? loan.fine_amount ?? 0);
    const overdueCard = !isHistory && Number(loan.overdue_days || 0) > 0
        ? `
            <div class="loan-meta" style="margin-top: 12px; padding: 10px 12px; border-radius: 14px; background: rgba(178, 71, 71, 0.10); border: 1px solid rgba(178, 71, 71, 0.18);">
                <div><strong>Days Overdue:</strong> ${loan.overdue_days}</div>
                <div><strong>Outstanding Fine:</strong> $${displayFineAmount.toFixed(2)}</div>
            </div>
        `
        : "";

    return `
        <article class="loan-item">
            <div class="loan-title">${loan.book_title || "Untitled Book"}</div>
            <div class="loan-meta">
                <div><strong>Copy:</strong> ${loan.copy_no || "Unknown"}</div>
                <div><strong>Loan ID:</strong> ${loan.loan_id || "Unknown"}</div>
                <div><strong>Loan Date:</strong> ${loan.loan_date || "Unknown"}</div>
                <div><strong>Due Date:</strong> ${loan.due_date || "Unknown"}</div>
                ${returnLine}
            </div>
            ${overdueCard}
        </article>
    `;
}

function renderLoanSection(selector, loans, emptyMessage, isHistory = false) {
    const container = $(selector);
    container.empty();

    if (!loans.length) {
        container.append(`<div class="loan-empty">${emptyMessage}</div>`);
        return;
    }

    loans.forEach((loan) => {
        container.append(createLoanItem(loan, isHistory));
    });
}

async function loadLoansFromServer() {
    const response = await fetch(`/api/loans?t=${Date.now()}`);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== 'mongodb') {
        throw new Error(result.message || "Unable to load loan data.");
    }

    if (result.collection !== "members") {
        throw new Error("Loan data did not come from the members collection.");
    }

    saveSessionUser({
        user_id: result.user._id,
        login_id: result.user._id,
        role: result.user.role,
        name: result.user.name,
        email: result.user.email,
        profile: result.user
    });

    return {
        user: result.user,
        currentLoans: normalizeLoanEntries(result.current_loans),
        loanHistory: normalizeLoanEntries(result.loan_history)
    };
}

$(document).ready(async function () {
    const cachedMember = getSessionMember();

    if (cachedMember) {
        if (String(cachedMember.role || "").trim().toLowerCase() === "librarian") {
            window.location.replace("/Loans/librarian_loans.html");
            return;
        }
        const cachedCurrentLoans = normalizeLoanEntries(cachedMember.current_loans);
        const cachedLoanHistory = normalizeLoanEntries(cachedMember.loan_history);

        $("#loansMemberName").text(cachedMember.name || cachedMember._id || "Current User");
        $("#currentLoanCount").text(String(cachedCurrentLoans.length));
        $("#loanHistoryCount").text(String(cachedLoanHistory.length));
        $("#loanOutstandingBalance").text(`$${Number(cachedMember.outstanding_balance ?? cachedMember.late_fee ?? 0).toFixed(2)}`);
        $("#loansStatusMessage").text(`Showing the tracked signed-in user while MongoDB refreshes. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);

        renderLoanSection("#currentLoansList", cachedCurrentLoans, "No books are currently loaned on this account.");
        renderLoanSection("#loanHistoryList", cachedLoanHistory, "No loan history is recorded yet.", true);
    }

    try {
        await syncTrackedSession();
        await reportCurrentPage("/Loans/loans.html");
        startSessionHeartbeat("/Loans/loans.html");
        const syncedMember = getSessionMember();
        if (syncedMember && String(syncedMember.role || "").trim().toLowerCase() === "librarian") {
            window.location.replace("/Loans/librarian_loans.html");
            return;
        }

        const { user, currentLoans, loanHistory } = await loadLoansFromServer();

        $("#loansMemberName").text(user.name);
        $("#currentLoanCount").text(String(currentLoans.length));
        $("#loanHistoryCount").text(String(loanHistory.length));
        $("#loanOutstandingBalance").text(`$${Number(user.outstanding_balance ?? user.late_fee ?? 0).toFixed(2)}`);
        $("#loansStatusMessage").text(`Live loan data loaded from MongoDB (Library.members). Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);

        renderLoanSection("#currentLoansList", currentLoans, "No books are currently loaned on this account.");
        renderLoanSection("#loanHistoryList", loanHistory, "No loan history is recorded yet.", true);
    } catch (error) {
        console.error("Error loading loans from server: ", error);
        if (cachedMember) {
            $("#loansStatusMessage").text(`Showing tracked user. MongoDB refresh failed: ${error.message}`);
            return;
        }

        $("#loansMemberName").text("Loan Load Failed");
        $("#loansStatusMessage").text(`MongoDB request failed: ${error.message}`);
        alert("You are not logged in. Redirecting to login page...");
        window.location.href = "/LoginPage/Login.html";
        return;
    }

    $("#logoutBtn").on("click", async function (event) {
        event.preventDefault();
        await performLogout("/LoginPage/Login.html");
    });
});
