import {
    clearSelectedMember,
    getLoansPagePath,
    getSelectedMember,
    getSelectedMemberId,
    getSessionMember,
    getSessionTimeoutLabel,
    getTrackedSessionId,
    normalizeLoanEntries,
    performLogout,
    reportCurrentPage,
    saveSelectedMember,
    startSessionHeartbeat,
    syncTrackedSession
} from "./JS_members.js";

function formatRole(role) {
    const normalizedRole = String(role || "").trim().toLowerCase();
    return normalizedRole
        ? normalizedRole.charAt(0).toUpperCase() + normalizedRole.slice(1)
        : "Member";
}

function formatText(value, fallback = "Not Provided") {
    const normalized = String(value || "").trim();
    return normalized || fallback;
}

function formatCurrency(value) {
    return `$${Number(value || 0).toFixed(2)}`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function createLoanCardMarkup(loan, isHistory = false) {
    const returnedLine = isHistory && loan.return_date
        ? `<div><strong>Returned:</strong> ${escapeHtml(loan.return_date)}</div>`
        : "";
    const displayFineAmount = Number(loan.outstanding_fine_amount ?? loan.fine_amount ?? 0);
    const overdueCard = !isHistory && Number(loan.overdue_days || 0) > 0
        ? `
            <div class="loan-meta" style="margin-top: 12px; padding: 10px 12px; border-radius: 14px; background: rgba(178, 71, 71, 0.10); border: 1px solid rgba(178, 71, 71, 0.18);">
                <div><strong>Days Overdue:</strong> ${escapeHtml(loan.overdue_days)}</div>
                <div><strong>Outstanding Fine:</strong> ${escapeHtml(formatCurrency(displayFineAmount))}</div>
            </div>
        `
        : "";

    return `
        <article class="loan-item">
            <div><strong>${escapeHtml(loan.book_title || "Untitled Book")}</strong></div>
            <div class="loan-meta">
                <div><strong>Copy:</strong> ${escapeHtml(loan.copy_no || "Unknown")}</div>
                <div><strong>Loan ID:</strong> ${escapeHtml(loan.loan_id || "Unknown")}</div>
                <div><strong>Loan Date:</strong> ${escapeHtml(loan.loan_date || "Unknown")}</div>
                <div><strong>Due Date:</strong> ${escapeHtml(loan.due_date || "Unknown")}</div>
                ${returnedLine}
            </div>
            ${overdueCard}
        </article>
    `;
}

function createPaymentCardMarkup(payment) {
    return `
        <article class="loan-item">
            <div><strong>${escapeHtml(formatCurrency(payment.amount_paid))}</strong></div>
            <div class="loan-meta">
                <div><strong>Receipt:</strong> ${escapeHtml(payment.receipt_no || "Pending")}</div>
                <div><strong>Method:</strong> ${escapeHtml(payment.payment_method || "counter_cash")}</div>
                <div><strong>Processed By:</strong> ${escapeHtml(payment.processed_by || "Unknown")}</div>
                <div><strong>Processed At:</strong> ${escapeHtml(payment.processed_at || "Unknown")}</div>
                <div><strong>Notes:</strong> ${escapeHtml(payment.notes || "None")}</div>
            </div>
        </article>
    `;
}

function renderLoanCards(selector, loans, emptyMessage, isHistory = false) {
    const container = $(selector);
    if (!container.length) {
        return;
    }

    if (!loans.length) {
        container.html(`<div class="empty-state">${escapeHtml(emptyMessage)}</div>`);
        return;
    }

    container.html(loans.map((loan) => createLoanCardMarkup(loan, isHistory)).join(""));
}

function renderPaymentCards(payments) {
    const container = $("#memberPaymentHistoryList");
    if (!container.length) {
        return;
    }

    if (!payments.length) {
        container.html('<div class="empty-state">No fee payments have been recorded for this member.</div>');
        return;
    }

    container.html(payments.map((payment) => createPaymentCardMarkup(payment)).join(""));
}

function updatePaymentFormState(member) {
    const outstandingBalance = Number(member?.outstanding_balance ?? member?.late_fee ?? 0);
    const submitButton = $("#memberPaymentSubmit");
    const amountInput = $("#paymentAmountInput");
    const status = $("#memberPaymentStatus");

    submitButton.prop("disabled", outstandingBalance <= 0);
    amountInput.attr("max", outstandingBalance > 0 ? outstandingBalance.toFixed(2) : null);

    if (outstandingBalance <= 0) {
        status.text("This member has no outstanding late-fee balance.");
    } else if (!status.text().trim() || status.text().includes("no outstanding")) {
        status.text(`Outstanding balance available for payment: ${formatCurrency(outstandingBalance)}.`);
    }
}

function renderMemberDetails(member, sourceLabel = "browser storage") {
    if (!member) {
        return false;
    }

    const roleLabel = formatRole(member.role);
    const currentLoans = normalizeLoanEntries(member.current_loans);
    const loanHistory = normalizeLoanEntries(member.loan_history);
    const paymentHistory = Array.isArray(member.payment_history) ? member.payment_history : [];

    $("#memberNameDisplay").text(formatText(member.name, "Unknown Member"));
    $("#memberIdDisplay").text(formatText(member._id, "Unknown"));
    $("#memberEmailDisplay").text(formatText(member.email));
    $("#memberPhoneDisplay").text(formatText(member.phone));
    $("#memberCurrentLoanCount").text(String(currentLoans.length));
    $("#memberLoanHistoryCount").text(String(loanHistory.length));
    $("#memberLateFeeDisplay").text(formatCurrency(member.late_fee));
    $("#memberOutstandingBalanceDisplay").text(formatCurrency(member.outstanding_balance ?? member.late_fee));
    $("#memberTotalChargesDisplay").text(formatCurrency(member.outstanding_balance ?? member.late_fee));
    $("#memberTotalPaymentsDisplay").text(formatCurrency(member.total_payments));
    $("#memberDescription").text(`${roleLabel} profile loaded from ${sourceLabel}.`);

    renderLoanCards("#memberCurrentLoansList", currentLoans, "This member has no current loans.");
    renderLoanCards("#memberLoanHistoryList", loanHistory, "This member has no loan history.", true);
    renderPaymentCards(paymentHistory);
    updatePaymentFormState(member);
    return true;
}

async function loadMemberById(memberId) {
    const sessionId = getTrackedSessionId();
    const response = await fetch(`/api/member-details?memberId=${encodeURIComponent(memberId)}&sessionId=${encodeURIComponent(sessionId)}&t=${Date.now()}`);
    const text = await response.text();
    let result;

    try {
        result = JSON.parse(text);
    } catch (error) {
        console.error("[MEMBER DASH] Invalid member detail response:", text);
        throw new Error("Invalid JSON response from server");
    }

    if (!response.ok || !result.ok || result.source !== "mongodb" || !result.member) {
        throw new Error(result.message || "Unable to load member details.");
    }

    saveSelectedMember(result.member);
    return result.member;
}

async function processFeePayment(memberId) {
    const amountInput = $("#paymentAmountInput");
    const methodInput = $("#paymentMethodSelect");
    const notesInput = $("#paymentNotesInput");
    const status = $("#memberPaymentStatus");
    const submitButton = $("#memberPaymentSubmit");

    const amountValue = Number(amountInput.val());
    if (!Number.isFinite(amountValue) || amountValue <= 0) {
        throw new Error("Enter a payment amount greater than zero.");
    }

    submitButton.prop("disabled", true);
    status.text("Processing payment in MongoDB...");
    try {
        const response = await fetch("/api/fee-payments", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            cache: "no-store",
            body: JSON.stringify({
                memberId,
                sessionId: getTrackedSessionId(),
                amountPaid: amountValue,
                paymentMethod: methodInput.val(),
                notes: notesInput.val()
            })
        });
        const responseText = await response.text();
        let result;

        try {
            result = JSON.parse(responseText);
        } catch (error) {
            console.error("[MEMBER DASH] Invalid fee payment response:", responseText);
            throw new Error("Invalid JSON response from server.");
        }

        if (!response.ok || !result.ok || !result.member) {
            throw new Error(result.message || "Unable to process payment.");
        }

        const freshMember = await loadMemberById(memberId);
        notesInput.val("");
        amountInput.val("");
        saveSelectedMember(freshMember);
        renderMemberDetails(freshMember, "the MongoDB members collection");
        status.text(
            `Payment recorded. Receipt ${result.payment?.receipt_no || "generated"} for ${formatCurrency(result.payment?.amount_paid || amountValue)}. `
            + `Outstanding balance changed from ${formatCurrency(result.previousOutstandingBalance)} to ${formatCurrency(result.newOutstandingBalance)}.`
        );
        return freshMember;
    } finally {
        submitButton.prop("disabled", false);
    }
}

function getPaymentRequestFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const rawAmount = String(params.get("paymentAmount") || "").trim();
    const rawMethod = String(params.get("paymentMethod") || "").trim();
    const rawNotes = String(params.get("paymentNotes") || "").trim();

    if (!rawAmount) {
        return null;
    }

    return {
        amount: rawAmount,
        method: rawMethod || "counter_cash",
        notes: rawNotes
    };
}

function clearPaymentRequestFromUrl() {
    const cleanUrl = `${window.location.pathname}${window.location.hash || ""}`;
    window.history.replaceState({}, document.title, cleanUrl);
}

async function handlePaymentSubmission(selectedMemberId, selectedMember) {
    if (!selectedMemberId) {
        $("#memberPaymentStatus").text("Select a member before processing a payment.");
        return;
    }

    try {
        await processFeePayment(selectedMemberId);
    } catch (error) {
        console.error("Fee payment failed:", error);
        $("#memberPaymentStatus").text(error.message || "Payment processing failed.");
        const latestSelectedMember = getSelectedMember() || selectedMember || null;
        const outstandingBalance = Number(latestSelectedMember?.outstanding_balance ?? latestSelectedMember?.late_fee ?? 0);
        $("#memberPaymentSubmit").prop("disabled", outstandingBalance <= 0);
    }
}

window.handleMemberPaymentSubmission = async function handleMemberPaymentSubmissionEvent(event) {
    if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
    }

    const selectedMember = getSelectedMember();
    const selectedMemberId = String(getSelectedMemberId() || (selectedMember && selectedMember._id) || "").trim();
    await handlePaymentSubmission(selectedMemberId, selectedMember);
    return false;
};

$(document).ready(async function () {
    const selectedMember = getSelectedMember();
    const selectedMemberId = String(getSelectedMemberId() || (selectedMember && selectedMember._id) || "").trim();
    let hasRenderedMember = false;
    let activeUser = getSessionMember();
    const pendingPaymentRequest = getPaymentRequestFromUrl();
    $(".member-tab[href='../Loans/loans.html']").attr("href", getLoansPagePath());

    $("#logoutBtn").on("click", async function (event) {
        event.preventDefault();
        clearSelectedMember();
        await performLogout("/LoginPage/Login.html");
    });

    $("#memberPaymentForm").on("submit", async function (event) {
        event.preventDefault();
        await handlePaymentSubmission(selectedMemberId, selectedMember);
    });

    $("#memberPaymentSubmit").on("click", async function (event) {
        event.preventDefault();
        await handlePaymentSubmission(selectedMemberId, selectedMember);
    });

    if (selectedMember) {
        hasRenderedMember = renderMemberDetails(selectedMember, "browser storage");
        $("#memberPageStatus").text(`Showing the selected member from browser storage. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
    }

    if (!selectedMember && !selectedMemberId) {
        $("#memberPageStatus").text("No member was selected.");
        $("#memberNameDisplay").text("Search Required");
        $("#memberDescription").text("Open this page from the librarian member search results.");
        renderLoanCards("#memberCurrentLoansList", [], "No member selected.");
        renderLoanCards("#memberLoanHistoryList", [], "No member selected.");
        return;
    }

    if (pendingPaymentRequest && hasRenderedMember && selectedMemberId) {
        $("#paymentAmountInput").val(pendingPaymentRequest.amount);
        $("#paymentMethodSelect").val(pendingPaymentRequest.method);
        $("#paymentNotesInput").val(pendingPaymentRequest.notes);
    }

    try {
        await syncTrackedSession();
        activeUser = getSessionMember();

        if (!activeUser || String(activeUser.role || "").trim().toLowerCase() !== "librarian") {
            if (hasRenderedMember) {
                $("#memberPageStatus").text("Showing the selected member from browser storage.");
                return;
            }

            throw new Error("This page is only available to librarians.");
        }

        await reportCurrentPage("/LoginPage/member_dashboard.html");
        startSessionHeartbeat("/LoginPage/member_dashboard.html");
        $(".member-tab[href='../Loans/loans.html'], .member-tab[href='/Loans/librarian_loans.html']").attr("href", getLoansPagePath(activeUser));

        if (!selectedMemberId) {
            if (!hasRenderedMember) {
                throw new Error("No member ID is available.");
            }
            return;
        }

        const freshMember = await loadMemberById(selectedMemberId);
        renderMemberDetails(freshMember, "the MongoDB members collection");
        $("#memberPageStatus").text(`Selected member profile loaded from MongoDB. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
        clearSelectedMember();
        saveSelectedMember(freshMember);

        if (pendingPaymentRequest) {
            $("#paymentAmountInput").val(pendingPaymentRequest.amount);
            $("#paymentMethodSelect").val(pendingPaymentRequest.method);
            $("#paymentNotesInput").val(pendingPaymentRequest.notes);
            clearPaymentRequestFromUrl();
            await handlePaymentSubmission(selectedMemberId, freshMember);
        }
    } catch (error) {
        console.error("Error loading member details:", error);

        if ((!activeUser || String(activeUser.role || "").trim().toLowerCase() !== "librarian") && hasRenderedMember) {
            $("#memberPageStatus").text("Showing the selected member from browser storage.");
            return;
        }

        if (selectedMemberId && activeUser && String(activeUser.role || "").trim().toLowerCase() === "librarian") {
            try {
                const freshMember = await loadMemberById(selectedMemberId);
                renderMemberDetails(freshMember, "the MongoDB members collection");
                $("#memberPageStatus").text(`Selected member profile loaded from MongoDB. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
                clearSelectedMember();
                saveSelectedMember(freshMember);
                if (pendingPaymentRequest) {
                    $("#paymentAmountInput").val(pendingPaymentRequest.amount);
                    $("#paymentMethodSelect").val(pendingPaymentRequest.method);
                    $("#paymentNotesInput").val(pendingPaymentRequest.notes);
                    clearPaymentRequestFromUrl();
                    await handlePaymentSubmission(selectedMemberId, freshMember);
                }
                return;
            } catch (fallbackError) {
                console.error("Fallback member detail refresh failed:", fallbackError);
            }
        }

        if (String(error.message || "").includes("only available to librarians")) {
            if (!hasRenderedMember) {
                alert(error.message);
                window.location.href = "/LoginPage/dashboard.html";
                return;
            }

            $("#memberPageStatus").text("Showing the selected member from browser storage.");
            return;
        }

        if (hasRenderedMember) {
            $("#memberPageStatus").text(`Showing selected member. MongoDB refresh failed: ${error.message}`);
            return;
        }

        $("#memberPageStatus").text(`Member details failed: ${error.message}`);
        $("#memberNameDisplay").text("Load Error");
        $("#memberDescription").text("The selected member profile could not be loaded.");
        renderLoanCards("#memberCurrentLoansList", [], "Unable to load current loans.");
        renderLoanCards("#memberLoanHistoryList", [], "Unable to load loan history.");
    }

});
