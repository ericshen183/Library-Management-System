import { getLoansPagePath, getSessionMember, getSessionTimeoutLabel, performLogout, reportCurrentPage, saveSessionUser, startSessionHeartbeat, syncTrackedSession } from "../LoginPage/JS_members.js";

let allBooks = [];
const BOOKS_CACHE_KEY = "library_books_cache";
let activeSessionMember = null;

function getUserLoans() {
    const member = activeSessionMember || getSessionMember();
    if (!member || !member.current_loans) {
        return [];
    }

    if (Array.isArray(member.current_loans)) {
        return member.current_loans;
    }

    return [member.current_loans];
}

function isMemberRole(role) {
    return String(role || "").trim().toLowerCase() === "member";
}

function isLibrarianRole(role) {
    return String(role || "").trim().toLowerCase() === "librarian";
}

function getCurrentViewer() {
    return activeSessionMember || getSessionMember();
}

function isCopyOnLoan(copyStatus) {
    return String(copyStatus || "").trim().toLowerCase() === "on loan";
}

function getLoanActionLabel(copyNumber) {
    const userLoans = getUserLoans();
    const hasLoan = userLoans.some((loan) => loan && loan.copy_no === copyNumber);
    return hasLoan ? "Return Book" : "Ask to Loan";
}

function getLoanActionType(copyNumber) {
    return getLoanActionLabel(copyNumber) === "Return Book" ? "return" : "request";
}

function createCopyActionButton(bookId, copyNumber, viewerRole, copyStatus) {
    if (isMemberRole(viewerRole)) {
        const viewer = getCurrentViewer();
        const hasLateFees = Number(viewer && viewer.late_fee || 0) > 0;
        if (hasLateFees) {
            return `<button type="button" class="copy-action request-action-btn" data-book-id="${bookId || ""}" data-copy-no="${copyNumber}" data-action="request" disabled title="Outstanding late fees must be paid before requesting another book.">Ask to Loan</button>`;
        }

        return `<button type="button" class="copy-action request-action-btn" data-book-id="${bookId || ""}" data-copy-no="${copyNumber}" data-action="request" onclick="window.handleLibraryCopyAction && window.handleLibraryCopyAction(this)">Ask to Loan</button>`;
    }

    if (isLibrarianRole(viewerRole)) {
        if (!isCopyOnLoan(copyStatus)) {
            return "";
        }

        return `<button type="button" class="copy-action return-action-btn" data-book-id="${bookId || ""}" data-copy-no="${copyNumber}" data-action="return" onclick="window.handleLibraryCopyAction && window.handleLibraryCopyAction(this)">Return Book</button>`;
    }

    return `<button type="button" class="copy-action login-required-btn" data-book-id="${bookId || ""}" data-copy-no="${copyNumber}" data-action="login-required" disabled title="Sign in to manage loans.">Login Required</button>`;
}

function getAuthorName(book) {
    if (!book || !book.author) {
        return "Unknown";
    }

    if (typeof book.author === "string") {
        return book.author;
    }

    return book.author.name || "Unknown";
}

function getBookCopies(book) {
    if (!book) {
        return [];
    }

    if (Array.isArray(book.copies)) {
        return book.copies;
    }

    if (Array.isArray(book.Copies)) {
        return book.Copies;
    }

    return [];
}

function levenshteinDistance(a, b) {
    const rows = a.length + 1;
    const cols = b.length + 1;
    const matrix = Array.from({ length: rows }, () => Array(cols).fill(0));

    for (let i = 0; i < rows; i += 1) {
        matrix[i][0] = i;
    }

    for (let j = 0; j < cols; j += 1) {
        matrix[0][j] = j;
    }

    for (let i = 1; i < rows; i += 1) {
        for (let j = 1; j < cols; j += 1) {
            const cost = a[i - 1] === b[j - 1] ? 0 : 1;
            matrix[i][j] = Math.min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost
            );
        }
    }

    return matrix[a.length][b.length];
}

function getBookSearchText(book) {
    const copyNumbers = getBookCopies(book).map((copy) => copy.copy_no || copy.Copy_no || "").join(" ");
    return [
        book.title,
        getAuthorName(book),
        book._id,
        String(book.year || ""),
        copyNumbers
    ].join(" ").toLowerCase();
}

function scoreBook(book, query) {
    const normalizedQuery = query.toLowerCase();
    const title = (book.title || "").toLowerCase();
    const author = getAuthorName(book).toLowerCase();
    const isbn = String(book._id || "").toLowerCase();
    const copies = getBookCopies(book).map((copy) => String(copy.copy_no || copy.Copy_no || "").toLowerCase());
    const haystack = getBookSearchText(book);

    if (!normalizedQuery) {
        return 0;
    }

    if (title === normalizedQuery) {
        return 1000;
    }

    if (title.startsWith(normalizedQuery)) {
        return 900;
    }

    if (title.includes(normalizedQuery)) {
        return 800;
    }

    if (author.includes(normalizedQuery)) {
        return 700;
    }

    if (isbn.includes(normalizedQuery) || copies.some((copyNo) => copyNo.includes(normalizedQuery))) {
        return 650;
    }

    if (haystack.includes(normalizedQuery)) {
        return 600;
    }

    const distance = Math.min(
        levenshteinDistance(normalizedQuery, title || normalizedQuery),
        author ? levenshteinDistance(normalizedQuery, author) : normalizedQuery.length
    );

    return Math.max(1, 100 - distance * 10);
}

function renderBooks(books, query) {
    const bookGrid = $("#bookGrid");
    const status = $("#libraryStatus");
    const resultCount = $("#resultCount");
    const closestBanner = $("#closestMatchBanner");
    const closestText = $("#closestMatchText");

    bookGrid.empty();
    resultCount.text(String(books.length));

    if (!books.length) {
        closestBanner.attr("hidden", true);
        status.text("No books matched your search.");
        bookGrid.append('<div class="empty-state">No matching books found. Try a title, author, ISBN, or copy number.</div>');
        return;
    }

    const viewer = getCurrentViewer();
    const viewerIsMember = isMemberRole(viewer && viewer.role);
    const viewerIsLibrarian = isLibrarianRole(viewer && viewer.role);
    const viewerRole = viewer && viewer.role;

    status.text(query ? `Showing the closest matches for "${query}".` : "Showing every book currently stored in MongoDB.");

    if (query) {
        closestBanner.attr("hidden", false);
        closestText.text(books[0].title);
    } else {
        closestBanner.attr("hidden", true);
    }

    books.forEach((book, index) => {
        const copiesMarkup = getBookCopies(book).map((copy) => {
            const copyNumber = copy.copy_no || copy.Copy_no || "Unknown";
            const copyStatus = copy.status || copy.availability || copy.Availability || "Unknown";
            const statusClass = String(copyStatus).toLowerCase().replace(/\s+/g, "-");
            const actionMarkup = createCopyActionButton(book._id || "", copyNumber, viewerRole, copyStatus);

            return `
                <div class="copy-item">
                    <div class="copy-details">
                        <span>${copyNumber}</span>
                        <span class="copy-status ${statusClass}">${copyStatus}</span>
                    </div>
                    ${actionMarkup}
                </div>
            `;
        }).join("");

        bookGrid.append(`
            <article class="book-card ${query && index === 0 ? "closest" : ""}">
                <h3>${book.title || "Untitled Book"}</h3>
                <div class="book-meta">
                    <div><strong>Author:</strong> ${getAuthorName(book)}</div>
                    <div><strong>Year:</strong> ${book.year || "Unknown"}</div>
                    <div><strong>ISBN:</strong> ${book._id || "Unknown"}</div>
                </div>
                <div class="copy-list">${copiesMarkup}</div>
            </article>
        `);
    });
}

function filterBooks(query) {
    const trimmedQuery = query.trim();

    if (!trimmedQuery) {
        renderBooks(allBooks, "");
        return;
    }

    const rankedBooks = allBooks
        .map((book) => ({ book, score: scoreBook(book, trimmedQuery) }))
        .filter((entry) => entry.score > 0)
        .sort((left, right) => right.score - left.score || left.book.title.localeCompare(right.book.title))
        .map((entry) => entry.book);

    renderBooks(rankedBooks, trimmedQuery);
}

async function loadBooksFromServer() {
    const response = await fetch(`/api/books?t=${Date.now()}`);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== "mongodb") {
        throw new Error(result.message || "Unable to load books.");
    }

    if (result.collection !== "books") {
        throw new Error("Books did not come from the books collection.");
    }

    allBooks = result.books || [];
    localStorage.setItem(BOOKS_CACHE_KEY, JSON.stringify(allBooks));
    $("#libraryStatus").text("Showing every book currently stored in MongoDB.");
    $("#librarySourceMessage").text(`Loaded ${allBooks.length} books from MongoDB (Library.books). Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
    renderBooks(allBooks, "");
}

async function handleCopyAction(button) {
    let viewer = null;

    try {
        viewer = await syncTrackedSession();
        activeSessionMember = viewer;
    } catch (error) {
        $("#librarySourceMessage").text(`You need an active session before borrowing books: ${error.message}`);
        alert(`You need an active session before borrowing books: ${error.message}`);
        return;
    }

    const action = button.getAttribute("data-action");
    const bookId = button.getAttribute("data-book-id");
    const copyNo = button.getAttribute("data-copy-no");

    if (action === "request" && !isMemberRole(viewer && viewer.role)) {
        const roleMessage = isLibrarianRole(viewer && viewer.role)
            ? "Only members can ask to loan books."
            : "Only signed-in members can request books.";
        $("#librarySourceMessage").text(roleMessage);
        alert(roleMessage);
        return;
    }

    if (action === "request" && Number(viewer && viewer.late_fee || 0) > 0) {
        const feeMessage = `Outstanding late fees of $${Number(viewer.late_fee || 0).toFixed(2)} must be paid before requesting another book.`;
        $("#librarySourceMessage").text(feeMessage);
        alert(feeMessage);
        return;
    }

    if (action === "return" && !isLibrarianRole(viewer && viewer.role)) {
        const roleMessage = "Only librarians can return books.";
        $("#librarySourceMessage").text(roleMessage);
        alert(roleMessage);
        return;
    }

    if (!copyNo) {
        return;
    }

    button.disabled = true;

    try {
        let response;
        if (action === "return") {
            response = await fetch("/api/return-book", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ copyNo })
            });
        } else {
            response = await fetch("/api/loan-requests", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ bookId, copyNo })
            });
        }

        const result = await response.json();
        if (!response.ok || !result.ok) {
            throw new Error(result.message || "Action could not be completed.");
        }

        activeSessionMember = await syncTrackedSession();
        await loadBooksFromServer();
        if (action === "return") {
            $("#librarySourceMessage").text(`Returned ${copyNo} successfully.`);
        } else {
            $("#librarySourceMessage").text(`Loan request for ${copyNo} was saved to MongoDB.`);
        }
    } catch (error) {
        $("#librarySourceMessage").text(error.message);
        alert(error.message);
    } finally {
        button.disabled = false;
    }
}

$(document).ready(async function() {
    const cachedMember = getSessionMember();
    const cachedBooks = localStorage.getItem(BOOKS_CACHE_KEY);

    if (cachedMember) {
        activeSessionMember = cachedMember;
        $("#sessionName").text(cachedMember.name || cachedMember._id || "Current User");
        $("#librarySourceMessage").text(`Showing the tracked signed-in user while MongoDB refreshes. Session timeout: ${getSessionTimeoutLabel()} of inactivity.`);
    }

    $(".library-tab[href='../Loans/loans.html']").attr("href", getLoansPagePath(cachedMember));

    if (cachedBooks) {
        try {
            allBooks = JSON.parse(cachedBooks);
            renderBooks(allBooks, "");
            $("#libraryStatus").text("Showing cached books while MongoDB refreshes.");
        } catch (error) {
            console.error("Unable to restore cached books:", error);
        }
    }

    try {
        try {
            activeSessionMember = await syncTrackedSession();
            await reportCurrentPage("/Book%20search/library.html");
            startSessionHeartbeat("/Book%20search/library.html");
            $(".library-tab[href='../Loans/loans.html'], .library-tab[href='/Loans/librarian_loans.html']").attr("href", getLoansPagePath(activeSessionMember));
        } catch (sessionError) {
            console.error("Error syncing session for library page: ", sessionError);
            if (!cachedMember) {
                $("#sessionName").text("Session Unavailable");
            }
        }

        await loadBooksFromServer();
    } catch (error) {
        console.error("Error loading books from server: ", error);
        if (cachedMember) {
            $("#libraryStatus").text("Unable to refresh the library from MongoDB right now.");
            $("#librarySourceMessage").text(`Tracked user loaded, but books could not be read from Library.books: ${error.message}`);
            return;
        }

        $("#libraryStatus").text("Unable to load the library right now.");
        $("#librarySourceMessage").text(`MongoDB books request failed: ${error.message}`);
        return;
    }

    $("#bookSearchInput").on("input", function() {
        filterBooks($(this).val());
    });

    $(document).on("click", ".copy-action", function() {
        if (this.disabled) {
            return;
        }
        handleCopyAction(this);
    });

    $("#logoutBtn").on("click", async function(event) {
        event.preventDefault();
        await performLogout("/LoginPage/Login.html");
    });
});

window.handleLibraryCopyAction = function(button) {
    if (!button || button.disabled) {
        return;
    }

    handleCopyAction(button);
};
