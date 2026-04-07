import { clearSession, getSessionMember, reportCurrentPage, saveSessionUser, syncTrackedSession } from "../LoginPage/JS_members.js";

let allBooks = [];

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
    const copyNumbers = (book.copies || []).map((copy) => copy.copy_no).join(" ");
    return [
        book.title,
        book.author?.name,
        book._id,
        String(book.year || ""),
        copyNumbers
    ].join(" ").toLowerCase();
}

function scoreBook(book, query) {
    const normalizedQuery = query.toLowerCase();
    const title = (book.title || "").toLowerCase();
    const author = (book.author?.name || "").toLowerCase();
    const isbn = String(book._id || "").toLowerCase();
    const copies = (book.copies || []).map((copy) => copy.copy_no.toLowerCase());
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

    status.text(query ? `Showing the closest matches for "${query}".` : "Showing every book currently stored in MongoDB.");

    if (query) {
        closestBanner.attr("hidden", false);
        closestText.text(books[0].title);
    } else {
        closestBanner.attr("hidden", true);
    }

    books.forEach((book, index) => {
        const copiesMarkup = (book.copies || []).map((copy) => {
            const statusClass = copy.status.toLowerCase().replace(/\s+/g, "-");
            return `
                <div class="copy-item">
                    <span>${copy.copy_no}</span>
                    <span class="copy-status ${statusClass}">${copy.status}</span>
                </div>
            `;
        }).join("");

        bookGrid.append(`
            <article class="book-card ${query && index === 0 ? "closest" : ""}">
                <h3>${book.title}</h3>
                <div class="book-meta">
                    <div><strong>Author:</strong> ${book.author?.name || "Unknown"}</div>
                    <div><strong>Year:</strong> ${book.year || "Unknown"}</div>
                    <div><strong>ISBN:</strong> ${book._id}</div>
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

async function loadLibraryPageData() {
    const response = await fetch(`/api/library-data?t=${Date.now()}`);
    const result = await response.json();

    if (!response.ok || !result.ok || result.source !== "mongodb") {
        throw new Error(result.message || "Unable to load library data.");
    }

    if (result.collection !== "books") {
        throw new Error("Library data did not come from the books collection.");
    }

    saveSessionUser({
        user_id: result.user._id,
        login_id: result.user._id,
        role: result.user.role,
        name: result.user.name,
        email: result.user.email,
        profile: result.user
    });

    allBooks = result.books || [];
    $("#sessionName").text(result.user.name || result.user._id || "Current User");
    $("#libraryStatus").text("Showing every book currently stored in MongoDB.");
    $("#librarySourceMessage").text(`Loaded ${allBooks.length} books from MongoDB (Library.books) for ${result.user._id}.`);
    renderBooks(allBooks, "");
}

$(document).ready(async function() {
    const cachedMember = getSessionMember();

    if (cachedMember) {
        $("#sessionName").text(cachedMember.name || cachedMember._id || "Current User");
        $("#librarySourceMessage").text("Showing the tracked signed-in user while MongoDB refreshes.");
    }

    try {
        await syncTrackedSession();
        await reportCurrentPage("/Book%20search/library.html");
        await loadLibraryPageData();
    } catch (error) {
        console.error("Error loading library page: ", error);
        if (cachedMember) {
            $("#libraryStatus").text("Unable to refresh the library from MongoDB right now.");
            $("#librarySourceMessage").text(`Showing tracked user. MongoDB refresh failed: ${error.message}`);
            return;
        }

        $("#sessionName").text("Session Unavailable");
        $("#libraryStatus").text("Unable to load the library right now.");
        $("#librarySourceMessage").text(`MongoDB request failed: ${error.message}`);
        alert("You are not logged in. Redirecting to login page...");
        window.location.href = "/LoginPage/Login.html";
        return;
    }

    $("#bookSearchInput").on("input", function() {
        filterBooks($(this).val());
    });

    $("#logoutBtn").on("click", async function(event) {
        event.preventDefault();
        clearSession();
        try {
            await fetch('/api/logout', {
                method: 'POST'
            });
        } catch (error) {
            console.error("Error logging out: ", error);
        }
        window.location.href = "/LoginPage/Login.html";
    });
});
