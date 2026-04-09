import { getLoansPagePath, getSessionMember, getSessionTimeoutLabel, normalizeLoanEntries, performLogout, reportCurrentPage, saveSelectedMember, saveSessionUser, startSessionHeartbeat, syncTrackedSession } from "./JS_members.js";

function isLibrarianRole(role) {
    return String(role || "").trim().toLowerCase() === "librarian";
}

function toggleLibrarianSearch(isVisible) {
    const card = $("#librarianSearchCard");
    if (!card.length) {
        return;
    }

    if (isVisible) {
        card.prop("hidden", false).removeAttr("hidden").css("display", "block");
    } else {
        card.prop("hidden", true).attr("hidden", "hidden").css("display", "none");
    }
}

function toggleLibrarianDashboardMode(isLibrarian) {
    toggleLibrarianSearch(isLibrarian);
    $("#currentAccountCard").toggleClass("librarian-hidden", isLibrarian);
    $("#accountSnapshotCard").toggleClass("librarian-hidden", isLibrarian);
    $("#accountHelpCard").toggleClass("librarian-hidden", isLibrarian);
    $("#currentAccountCard").css("display", isLibrarian ? "none" : "");
    $("#accountSnapshotCard").css("display", isLibrarian ? "none" : "");
    $("#accountHelpCard").css("display", isLibrarian ? "none" : "");
}

function renderDashboard(member) {
    const normalizedRole = String(member.role || "").trim().toLowerCase();
    const roleLabel = normalizedRole ? normalizedRole.charAt(0).toUpperCase() + normalizedRole.slice(1) : "User";
    const currentLoans = normalizeLoanEntries(member.current_loans);
    const loanHistory = normalizeLoanEntries(member.loan_history);

    $("#userNameDisplay").text(member.name);
    $("#userEmailDisplay").text(member.email);
    $("#userIdDisplay").text(member._id);
    $("#userRoleDisplay").text(roleLabel);
    $("#userPhoneDisplay").text(member.phone || "Not Provided");
    $("#userLoansDisplay").text(currentLoans.length);
    $("#userHistoryDisplay").text(loanHistory.length);
    $("#userFeeDisplay").text(`$${Number(member.late_fee || 0).toFixed(2)}`);
    $("#roleDescription").text(`${roleLabel} account connected to the live MongoDB-backed session. Sessions close after ${getSessionTimeoutLabel()} of inactivity.`);
    $("#accountStatusMessage").text(`Live account data loaded from MongoDB. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
}

async function loadAccountFromServer() {
    const response = await fetch(`/api/account?t=${Date.now()}`);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== "mongodb") {
        throw new Error(result.message || "Unable to load account data.");
    }

    if (!result.collection || (result.collection !== "members" && result.collection !== "librarians")) {
        throw new Error("Account data did not come from a profile collection.");
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

async function loadMatchingMembers(query) {
    console.log(`[LIBRARIAN SEARCH] Initiating member lookup for: "${query}"`);
    try {
        const response = await fetch(`/api/member-search?q=${encodeURIComponent(query)}&t=${Date.now()}`);
        const text = await response.text();
        let result;
        try {
            result = JSON.parse(text);
        } catch (parseErr) {
            console.error("[LIBRARIAN SEARCH] Failed to parse JSON:", text);
            throw new Error("Invalid JSON response from server");
        }
        console.log("[LIBRARIAN SEARCH] API raw response:", result);
        if (!response.ok || !result.ok || result.source !== "mongodb") {
            console.error("[LIBRARIAN SEARCH] API Error:", result.message || "Unknown error");
            throw new Error(result.message || "Unable to search members.");
        }
        if (result.collection !== "members") {
            console.warn("[LIBRARIAN SEARCH] Warning: Unexpected collection in result:", result.collection);
        }
        const members = Array.isArray(result.members) ? result.members : [];
        console.log(`[LIBRARIAN SEARCH] Found ${members.length} matches. Members:`, members);
        return members;
    } catch (err) {
        console.error("[LIBRARIAN SEARCH] Fetch or logic error:", err);
        throw err;
    }
}

function renderMemberResults(members) {
    console.log("[LIBRARIAN SEARCH] Rendering results for", members.length, "members.");
    const resultsContainer = $("#memberSearchResults");
    const listContainer = $("#memberResultList");

    if (!listContainer.length || !resultsContainer.length) {
        console.error("[LIBRARIAN SEARCH] Results containers not found in DOM!");
        return;
    }

    listContainer.empty();

    if (!members.length) {
        listContainer.html('<div class="member-empty">No matching members were found in the MongoDB members collection.</div>');
        resultsContainer.show();
        return;
    }

    members.forEach((member) => {
        try {
            const storedMember = {
                _id: member._id || "",
                name: member.name || "",
                email: member.email || "",
                phone: member.phone || "",
                role: member.role || "member",
                current_loans: member.current_loans || [],
                loan_history: member.loan_history || [],
                payment_history: member.payment_history || [],
                late_fee: member.late_fee || 0,
                outstanding_balance: member.outstanding_balance ?? member.late_fee ?? 0,
                total_overdue_charges: member.total_overdue_charges || 0,
                total_payments: member.total_payments || 0
            };

            const resultCard = $(`
                <article class="member-result-card">
                    <div style="font-weight: 700; font-size: 16px; color: #20323e;">${member.name || "Unknown Member"}</div>
                    <div class="member-result-meta">
                        <div><strong>ID:</strong> ${member._id || "Unknown"}</div>
                        <div><strong>Email:</strong> ${member.email || "Not Provided"}</div>
                    </div>
                    <button type="button" class="view-profile-btn" data-id="${member._id}" style="
                        margin-top: 10px;
                        border: 0;
                        border-radius: 999px;
                        padding: 10px 14px;
                        background: rgba(47, 143, 131, 0.12);
                        color: #1d645b;
                        font-weight: 700;
                        cursor: pointer;
                    ">View Profile</button>
                </article>
            `);

            resultCard.find(".view-profile-btn").on("click", function() {
                saveSelectedMember(storedMember);
                console.log("[LIBRARIAN SEARCH] Redirecting to stored member profile view.");
                window.location.href = "/LoginPage/member_dashboard.html";
            });

            listContainer.append(resultCard);
        } catch (e) {
            console.error("[LIBRARIAN SEARCH] Error rendering result card:", e);
        }
    });

    resultsContainer.show(); // Use show() instead of fadeIn() for immediate visibility
    console.log("[LIBRARIAN SEARCH] Results container should now be visible.");
}

async function openMemberSearchPage() {
    const query = String($("#memberSearchInput").val() || "").trim();
    const resultsContainer = $("#memberSearchResults");
    const statusLabel = $("#memberSearchStatus");

    if (!query) {
        statusLabel.text("Enter a member ID, name, or email address first.");
        resultsContainer.hide();
        return;
    }

    console.log(`[LIBRARIAN SEARCH] User clicked search for: "${query}"`);
    statusLabel.text("Searching the MongoDB members collection...").css("color", "#2f8f83");
    resultsContainer.hide();

    try {
        const members = await loadMatchingMembers(query);
        if (!members.length) {
            statusLabel.text(`No matching members were found for "${query}" in MongoDB.`).css("color", "#b24747");
            return;
        }

        statusLabel.text(`Found ${members.length} matching member${members.length === 1 ? "" : "s"} in MongoDB.`).css("color", "#20323e");
        renderMemberResults(members);
    } catch (error) {
        console.error("Error searching members:", error);
        statusLabel.text(`Member search failed: ${error.message}`).css("color", "#b24747");
        alert(`Search failed: ${error.message}`);
    }
}

$(document).ready(async function() {
    const cachedMember = getSessionMember();

    $("#memberSearchButton").on("click", openMemberSearchPage);
    $("#memberSearchInput").on("keydown", function(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            openMemberSearchPage();
        }
    });

    if (cachedMember) {
        renderDashboard(cachedMember);
        $("#accountStatusMessage").text(`Showing the tracked signed-in user while MongoDB refreshes. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
        toggleLibrarianDashboardMode(isLibrarianRole(cachedMember.role));
    }

    $(".dashboard-tab[href='../Loans/loans.html']").attr("href", getLoansPagePath(cachedMember));

    try {
        await syncTrackedSession();
        await reportCurrentPage("/LoginPage/dashboard.html");
        startSessionHeartbeat("/LoginPage/dashboard.html");
        const member = await loadAccountFromServer();
        $(".dashboard-tab[href='../Loans/loans.html'], .dashboard-tab[href='/Loans/librarian_loans.html']").attr("href", getLoansPagePath(member));
        renderDashboard(member);
        $("#accountStatusMessage").text(`Live account data loaded from MongoDB (Library.${isLibrarianRole(member.role) ? "librarians" : "members"}). Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);

        if (isLibrarianRole(member.role)) {
            toggleLibrarianDashboardMode(true);
            $("#roleDescription").text(`Librarian account connected to the live MongoDB-backed session. Sessions close after ${getSessionTimeoutLabel()} of inactivity. Search a member below to open their details page.`);
            $("#memberSearchStatus").text("Enter a member ID, name, or email address, then open the matching member details page.");
        } else {
            toggleLibrarianDashboardMode(false);
        }
    } catch (error) {
        console.error("Error loading account from server:", error);
        if (cachedMember) {
            $("#accountStatusMessage").text(`Showing tracked user. MongoDB refresh failed: ${error.message}`);
            return;
        }

        $("#accountStatusMessage").text(`Account data could not be loaded from MongoDB: ${error.message}`);
        $("#userNameDisplay").text("Account Load Failed");
        $("#roleDescription").text("The dashboard could not fetch the current session user from the database.");
        alert("You are not logged in. Redirecting to login page...");
        window.location.href = "/LoginPage/Login.html";
        return;
    }

    $("#logoutBtn").on("click", async function(event) {
        event.preventDefault();
        await performLogout("/LoginPage/Login.html");
    });
});
window.openMemberSearchPage = openMemberSearchPage;
