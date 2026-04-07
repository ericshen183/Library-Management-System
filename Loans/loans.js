import { clearSession, getSessionMember, normalizeLoanEntries, reportCurrentPage, saveSessionUser, syncTrackedSession } from "../LoginPage/JS_members.js";

function createLoanItem(loan, isHistory) {
    const returnLine = isHistory && loan.return_date
        ? `<div><strong>Returned:</strong> ${loan.return_date}</div>`
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

$(document).ready(async function() {
    const cachedMember = getSessionMember();

    if (cachedMember) {
        const cachedCurrentLoans = normalizeLoanEntries(cachedMember.current_loans);
        const cachedLoanHistory = normalizeLoanEntries(cachedMember.loan_history);

        $("#loansMemberName").text(cachedMember.name || cachedMember._id || "Current User");
        $("#currentLoanCount").text(String(cachedCurrentLoans.length));
        $("#loanHistoryCount").text(String(cachedLoanHistory.length));
        $("#loansStatusMessage").text("Showing the tracked signed-in user while MongoDB refreshes.");

        renderLoanSection("#currentLoansList", cachedCurrentLoans, "No books are currently loaned on this account.");
        renderLoanSection("#loanHistoryList", cachedLoanHistory, "No loan history is recorded yet.", true);
    }

    try {
        await syncTrackedSession();
        await reportCurrentPage("/Loans/loans.html");
        const { user, currentLoans, loanHistory } = await loadLoansFromServer();

        $("#loansMemberName").text(user.name);
        $("#currentLoanCount").text(String(currentLoans.length));
        $("#loanHistoryCount").text(String(loanHistory.length));
        $("#loansStatusMessage").text("Live loan data loaded from MongoDB (Library.members).");

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

    $("#logoutBtn").on("click", async function(event) {
        event.preventDefault();
        clearSession();
        try {
            await fetch("/api/logout", {
                method: "POST"
            });
        } catch (error) {
            console.error("Error logging out: ", error);
        }
        window.location.href = "/LoginPage/Login.html";
    });
});
