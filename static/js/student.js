/**
 * Offline SQL Exam System - Student Workspace Controller
 * Features:
 * - CodeMirror 5 Integration (PostgreSQL Mode & Autocompletion)
 * - Anti-Copy-Paste Security Filter
 * - Tab Switch / Focus Loss Detection Logging
 * - IP-Binding Heartbeat & Kick Detection
 * - Live Query Execution & Dynamic Data Grid
 * - Answer Submission & Status Tracking
 * - Interactive Schema Browser
 */

let editor = null;
let currentQuestionIndex = 0;
let questionsData = [];
let submissionsMap = {};
let studentNim = "";
let studentSessionToken = "";
let heartbeatInterval = null;
let isLoggingOut = false;

// Initialize when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
    studentNim = document.body.dataset.nim || "";
    studentSessionToken = document.body.dataset.token || "";

    initCodeMirror();
    initThemeSelector();
    setupAntiCheat();
    setupExitModal();
    loadQuestions();
    loadErdSchema();
    initSidebarTabs();
    startHeartbeat();

    // Bind Action Buttons
    document.getElementById("btn-run-query").addEventListener("click", runQuery);
    document.getElementById("btn-submit-answer").addEventListener("click", submitAnswer);
    document.getElementById("btn-format-sql")?.addEventListener("click", formatQuery);
    document.getElementById("btn-open-erd-diagram")?.addEventListener("click", openErdDiagram);
});

// ==============================================================================
// 1. CodeMirror SQL Editor Initialization
// ==============================================================================
function initCodeMirror() {
    const textarea = document.getElementById("sql-editor");
    if (!textarea) return;

    editor = CodeMirror.fromTextArea(textarea, {
        mode: "text/x-pgsql",
        theme: THEME_CM_MAP[localStorage.getItem(THEME_STORAGE_KEY) || "dracula"] || "dracula",
        lineNumbers: true,
        indentWithTabs: false,
        smartIndent: true,
        indentUnit: 4,
        matchBrackets: true,
        autoCloseBrackets: true,
        lineWrapping: true,
        extraKeys: {
            "Ctrl-Space": "autocomplete",
            "Ctrl-Enter": () => runQuery(),
            "Cmd-Enter": () => runQuery()
        }
    });

    // Anti-Paste Protection on Editor
    editor.on("paste", (cm, e) => {
        e.preventDefault();
        showToast("Paste dilarang pada ujian ini! Silakan ketik query secara mandiri.", "danger");
        logCheatingAction("paste_attempt", "Mencoba melakukan paste pada editor SQL");
    });

    // Auto-trigger completion on typing words
    editor.on("inputRead", (cm, change) => {
        if (change.origin !== "+input") return;
        const text = change.text[0];
        if (/[a-zA-Z_]/.test(text) && !cm.state.completionActive) {
            CodeMirror.commands.autocomplete(cm, null, { completeSingle: false });
        }
    });
}

// ==============================================================================
// 1b. Global Theme System
// ==============================================================================
const THEME_STORAGE_KEY = "exam_global_theme";

// Maps website theme key -> CodeMirror theme name
const THEME_CM_MAP = {
    "dracula":    "dracula",
    "monokai":    "monokai",
    "nord":       "nord",
    "material":   "material-darker",
    "gruvbox":    "gruvbox-dark",
    "solarized":  "solarized dark",
    "ayu":        "ayu-dark",
    "light":      "default",        // CodeMirror's built-in light theme
};

function initThemeSelector() {
    const select = document.getElementById("global-theme-select");
    if (!select) return;

    const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "dracula";
    applyGlobalTheme(savedTheme);

    select.addEventListener("change", () => {
        applyGlobalTheme(select.value);
        localStorage.setItem(THEME_STORAGE_KEY, select.value);
    });
}

function applyGlobalTheme(theme) {
    // Apply CSS variable theme to entire page
    document.documentElement.setAttribute("data-theme", theme);

    // Sync the dropdown
    const select = document.getElementById("global-theme-select");
    if (select) select.value = theme;

    // Sync CodeMirror editor theme
    if (editor) {
        const cmTheme = THEME_CM_MAP[theme] || "dracula";
        editor.setOption("theme", cmTheme);
    }
}


// ==============================================================================
// 2. Anti-Cheating & Security Controls
// ==============================================================================
function setupAntiCheat() {
    // Intercept general clipboard paste on page
    document.addEventListener("paste", (e) => {
        e.preventDefault();
        showToast("Paste dilarang pada sistem ujian!", "danger");
        logCheatingAction("paste_attempt", "Mencoba paste di luar editor");
    });

    // Detect tab/window switches
    document.addEventListener("visibilitychange", () => {
        if (isLoggingOut) return;
        if (document.hidden) {
            showToast("Peringatan: Berpindah tab atau aplikasi dicatat oleh sistem!", "danger");
            logCheatingAction("blur_tab", "Mahasiswa memindahkan fokus tab atau keluar jendela ujian");
        } else {
            logCheatingAction("focus_tab", "Mahasiswa kembali ke jendela ujian");
        }
    });

    window.addEventListener("blur", () => {
        if (isLoggingOut) return;
        logCheatingAction("blur_tab", "Window blur terdeteksi");
    });
}

function setupExitModal() {
    const triggerBtn = document.getElementById("btn-logout-trigger");
    const modal = document.getElementById("exit-confirm-modal");
    const cancelBtn = document.getElementById("btn-exit-cancel");
    const cancelXBtn = document.getElementById("btn-exit-cancel-x");
    const proceedBtn = document.getElementById("btn-exit-proceed");

    if (!triggerBtn || !modal) return;

    function openModal() {
        modal.style.display = "flex";
    }

    function closeModal() {
        modal.style.display = "none";
    }

    triggerBtn.addEventListener("click", (e) => {
        e.preventDefault();
        openModal();
    });

    if (cancelBtn) {
        cancelBtn.addEventListener("click", closeModal);
    }
    if (cancelXBtn) {
        cancelXBtn.addEventListener("click", closeModal);
    }

    // Close when clicking directly on the backdrop
    modal.addEventListener("click", (e) => {
        if (e.target === modal) {
            closeModal();
        }
    });

    // Close on ESC key
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && modal.style.display === "flex") {
            closeModal();
        }
    });

    // Set flag when student confirms exit so navigation does not fire false blur warning
    if (proceedBtn) {
        proceedBtn.addEventListener("click", () => {
            isLoggingOut = true;
        });
    }
}

async function logCheatingAction(actionType, details) {
    try {
        await fetch("/api/student/log-event", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                nim: studentNim,
                session_token: studentSessionToken,
                action_type: actionType,
                details: details
            })
        });
    } catch (err) {
        console.error("Log error:", err);
    }
}

// ==============================================================================
// 3. Heartbeat & Anti-Joki Session Monitoring
// ==============================================================================
function startHeartbeat() {
    heartbeatInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/student/heartbeat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    nim: studentNim,
                    session_token: studentSessionToken
                })
            });
            const data = await res.json();
            if (!data.valid) {
                clearInterval(heartbeatInterval);
                showKickModal(data.reason || "NIM Anda sedang diakses dari perangkat lain!");
            }
        } catch (err) {
            console.warn("Heartbeat offline or network hiccup:", err);
        }
    }, 15000); // Check every 15 seconds
}

function showKickModal(message) {
    if (editor) {
        editor.setOption("readOnly", "nocursor");
    }
    const modalHtml = `
        <div class="modal-backdrop" id="kick-modal" role="dialog" aria-modal="true">
            <div class="modal-dialog">
                <div class="modal-header">
                    <h3 style="color: #ef4444;">Sesi Ditangguhkan (Kicked)</h3>
                </div>
                <div class="modal-body">
                    <div class="alert-box alert-danger">
                        ${escapeHtml(message)}
                    </div>
                    <p style="margin-top: 0.5rem; color: #cbd5e1;">
                        Sistem mendeteksi aktivitas login baru dengan NIM Anda di perangkat lain atau IP yang berbeda. 
                        Untuk menjaga integritas ujian, sesi pada perangkat ini telah dinonaktifkan.
                    </p>
                </div>
                <div class="modal-footer">
                    <a href="/login" class="btn btn-primary">Kembali ke Halaman Login</a>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML("beforeend", modalHtml);
}

// ==============================================================================
// 4. Questions & Navigation
// ==============================================================================
async function loadQuestions() {
    try {
        const res = await fetch(`/api/student/questions?token=${encodeURIComponent(studentSessionToken)}`);
        const data = await res.json();
        if (data.questions) {
            questionsData = data.questions;
            submissionsMap = data.submissions || {};
            renderQuestionNav();
            selectQuestion(0);
        }
    } catch (err) {
        showToast("Gagal memuat daftar soal ujian.", "danger");
    }
}

function renderQuestionNav() {
    const container = document.getElementById("question-nav");
    if (!container) return;
    container.innerHTML = "";

    questionsData.forEach((q, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `q-btn ${idx === currentQuestionIndex ? "active" : ""}`;
        if (submissionsMap[q.id]) {
            btn.classList.add("submitted");
        }
        btn.textContent = q.question_number;
        btn.title = `Soal No. ${q.question_number}: ${q.title}`;
        btn.addEventListener("click", () => selectQuestion(idx));
        container.appendChild(btn);
    });
}

function selectQuestion(idx) {
    if (idx < 0 || idx >= questionsData.length) return;
    
    // Save current editor draft to memory before switching
    if (questionsData[currentQuestionIndex]) {
        questionsData[currentQuestionIndex].draftQuery = editor.getValue();
    }

    currentQuestionIndex = idx;
    const q = questionsData[idx];

    // Update active button
    const buttons = document.querySelectorAll(".q-btn");
    buttons.forEach((b, i) => {
        b.classList.toggle("active", i === idx);
    });

    // Update question details in UI
    document.getElementById("q-number").textContent = `Soal No. ${q.question_number}`;
    document.getElementById("q-title").textContent = q.title;
    document.getElementById("q-weight").textContent = `Bobot: ${q.score_weight} Poin`;
    
    // Convert basic markdown/newlines to safe HTML
    document.getElementById("q-description").innerHTML = formatMarkdown(q.description);

    // Populate editor with submitted query, draft, or clean placeholder
    const existingSubmission = submissionsMap[q.id];
    let queryToLoad = "";
    if (existingSubmission && existingSubmission.submitted_query) {
        queryToLoad = existingSubmission.submitted_query;
    } else if (q.draftQuery !== undefined) {
        queryToLoad = q.draftQuery;
    } else {
        queryToLoad = `-- Jawaban Soal No. ${q.question_number}: ${q.title}\nSELECT \n`;
    }

    editor.setValue(queryToLoad);
    editor.focus();
    editor.setCursor(editor.lineCount(), 0);

    // Update submission indicator tag
    const subTag = document.getElementById("submission-status-tag");
    if (subTag) {
        if (existingSubmission) {
            subTag.className = "status-badge badge-online";
            subTag.textContent = "Sudah Disubmit";
        } else {
            subTag.className = "status-badge badge-warning";
            subTag.textContent = "Belum Disubmit";
        }
    }
}

// ==============================================================================
// 5. Query Execution & Results Rendering
// ==============================================================================
async function runQuery() {
    const query = editor.getValue().trim();
    if (!query) {
        showToast("Editor query masih kosong.", "warning");
        return;
    }

    const runBtn = document.getElementById("btn-run-query");
    const outputContainer = document.getElementById("output-content");
    const metaContainer = document.getElementById("output-meta-text");

    runBtn.disabled = true;
    outputContainer.innerHTML = `
        <div class="state-box">
            <div class="spinner"></div>
            <p>Mengeksekusi query pada sandbox PostgreSQL...</p>
        </div>
    `;
    metaContainer.textContent = "Menjalankan...";

    try {
        const res = await fetch("/api/student/run-query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                nim: studentNim,
                session_token: studentSessionToken,
                query: query
            })
        });
        const data = await res.json();
        
        if (data.success) {
            renderResultTable(data.columns, data.rows, data.row_count, data.is_truncated);
            metaContainer.textContent = `${data.row_count} baris dikembalikan (${data.execution_time_ms} ms)`;
        } else {
            outputContainer.innerHTML = `
                <div class="state-box-error">
                    <strong>Error Eksekusi SQL:</strong><br>
                    ${escapeHtml(data.error || "Query gagal dieksekusi.")}
                </div>
            `;
            metaContainer.textContent = "Query Gagal";
        }
    } catch (err) {
        outputContainer.innerHTML = `
            <div class="state-box-error">
                <strong>Gagal Menghubungi Server:</strong><br>
                ${escapeHtml(err.message)}
            </div>
        `;
        metaContainer.textContent = "Koneksi Error";
    } finally {
        runBtn.disabled = false;
    }
}

function renderResultTable(columns, rows, count, isTruncated) {
    const container = document.getElementById("output-content");
    if (!columns || columns.length === 0) {
        container.innerHTML = `
            <div class="state-box state-box-empty">
                <p>Query berhasil dieksekusi (Tidak ada baris data kolom yang dikembalikan).</p>
            </div>
        `;
        return;
    }

    let html = `<div class="result-table-container"><table class="result-table"><thead><tr>`;
    columns.forEach(col => {
        html += `<th>${escapeHtml(col)}</th>`;
    });
    html += `</tr></thead><tbody>`;

    if (rows.length === 0) {
        html += `<tr><td colspan="${columns.length}" style="text-align: center; color: var(--text-subtle); padding: 2rem;">0 baris data ditemukan.</td></tr>`;
    } else {
        rows.forEach(row => {
            html += `<tr>`;
            row.forEach(val => {
                if (val === null) {
                    html += `<td><span class="val-null">NULL</span></td>`;
                } else {
                    html += `<td>${escapeHtml(String(val))}</td>`;
                }
            });
            html += `</tr>`;
        });
    }

    html += `</tbody></table>`;
    if (isTruncated) {
        html += `<div style="padding: 0.5rem 1rem; font-size: 0.75rem; color: #f59e0b; background: rgba(245, 158, 11, 0.1);">Tampilan dibatasi maksimal 500 baris untuk menjaga performa browser.</div>`;
    }
    html += `</div>`;
    container.innerHTML = html;
}

// ==============================================================================
// 6. Answer Submission
// ==============================================================================
async function submitAnswer() {
    const q = questionsData[currentQuestionIndex];
    if (!q) return;

    const query = editor.getValue().trim();
    if (!query) {
        showToast("Query jawaban tidak boleh kosong!", "warning");
        return;
    }

    const submitBtn = document.getElementById("btn-submit-answer");
    submitBtn.disabled = true;

    try {
        const res = await fetch("/api/student/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                nim: studentNim,
                session_token: studentSessionToken,
                question_id: q.id,
                query: query
            })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`Jawaban Soal No. ${q.question_number} berhasil disimpan!`, "success");
            submissionsMap[q.id] = { submitted_query: query };
            
            // Update button visual
            const activeBtn = document.querySelector(`.q-btn:nth-child(${currentQuestionIndex + 1})`);
            if (activeBtn) activeBtn.classList.add("submitted");

            const subTag = document.getElementById("submission-status-tag");
            if (subTag) {
                subTag.className = "status-badge badge-online";
                subTag.textContent = "Sudah Disubmit";
            }
        } else {
            showToast(data.message || "Gagal menyimpan jawaban.", "danger");
        }
    } catch (err) {
        showToast("Gagal mengirim jawaban ke server.", "danger");
    } finally {
        submitBtn.disabled = false;
    }
}

// ==============================================================================
// 7. Schema Browser
// ==============================================================================
// ==============================================================================
// 7b. Sidebar Tab Switcher
// ==============================================================================
function initSidebarTabs() {
    const tabBtns = document.querySelectorAll(".sidebar-tab-btn");
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            tabBtns.forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
            btn.classList.add("active");
            btn.setAttribute("aria-selected", "true");
            const target = btn.dataset.tab;
            document.querySelectorAll(".sidebar-tab-pane").forEach(pane => {
                pane.style.display = (pane.id === `tab-pane-${target}`) ? "flex" : "none";
            });
        });
    });
}

// ==============================================================================
// 7c. ERD Schema Renderer
// ==============================================================================
let _erdSchemaCache = null;
let _erdRelCache = [];
let _erdShowRel = false;

async function loadErdSchema() {
    const container = document.getElementById("erd-cards-container");
    if (!container) return;

    try {
        const res = await fetch("/api/student/schema-full", { credentials: "same-origin" });
        if (!res.ok) { renderErdError(container); return; }
        const data = await res.json();
        _erdSchemaCache = data.schema || {};
        _erdRelCache = data.relations || [];
        renderErdCards(container, _erdSchemaCache, _erdRelCache);
        bindErdSearch();
        bindErdRelToggle();
    } catch (_) {
        renderErdError(container);
    }
}

function renderErdError(container) {
    container.innerHTML = '<div style="padding:1rem;color:var(--danger,#ef4444);font-size:.82rem;">Gagal memuat schema ERD. Pastikan database sudah diinisialisasi.</div>';
}

function renderErdCards(container, schema, relations) {
    container.innerHTML = "";

    // Build quick lookup: table -> [incoming FKs], table -> [outgoing FKs]
    const incoming = {}; // to_table -> [{from_table, from_col, to_col}]
    const outgoing = {}; // from_table -> [{to_table, from_col, to_col}]
    const fkCols   = {}; // "table.col" -> true  (marks FK columns)

    relations.forEach(r => {
        if (!incoming[r.to_table])   incoming[r.to_table] = [];
        if (!outgoing[r.from_table]) outgoing[r.from_table] = [];
        incoming[r.to_table].push(r);
        outgoing[r.from_table].push(r);
        fkCols[`${r.from_table}.${r.from_col}`] = true;
    });

    const tableNames = Object.keys(schema).sort();

    tableNames.forEach(tbl => {
        const cols = schema[tbl];
        const hasRel = !!(incoming[tbl] || outgoing[tbl]);
        const card = document.createElement("div");
        card.className = "erd-table-card" + (hasRel ? " has-rel" : "") + " open";
        card.dataset.table = tbl;

        // ---- Header ----
        const header = document.createElement("div");
        header.className = "erd-table-header";
        header.innerHTML = `
            <span class="erd-table-name" title="${tbl}">${tbl}</span>
            <span class="erd-table-count">${cols.length} col</span>
            <svg class="erd-table-toggle" width="14" height="14"><use href="#si-chevron-down"/></svg>
        `;
        header.addEventListener("click", () => card.classList.toggle("open"));
        card.appendChild(header);

        // ---- Relation tags (outgoing FKs from this table) ----
        if (outgoing[tbl] && outgoing[tbl].length > 0) {
            const relWrap = document.createElement("div");
            relWrap.className = "erd-rel-tags erd-rel-section";
            relWrap.style.display = _erdShowRel ? "flex" : "none";
            outgoing[tbl].forEach(r => {
                const tag = document.createElement("span");
                tag.className = "erd-rel-tag";
                tag.title = `FK: ${r.from_table}.${r.from_col} → ${r.to_table}.${r.to_col}`;
                tag.textContent = `${r.from_col} → ${r.to_table}.${r.to_col}`;
                relWrap.appendChild(tag);
            });
            card.appendChild(relWrap);
        }

        // ---- Incoming relation tags ----
        if (incoming[tbl] && incoming[tbl].length > 0) {
            const relWrap = document.createElement("div");
            relWrap.className = "erd-rel-tags erd-rel-section";
            relWrap.style.cssText = `display:${_erdShowRel ? "flex" : "none"};background:color-mix(in srgb,var(--success,#10b981) 5%,transparent);`;
            incoming[tbl].forEach(r => {
                const tag = document.createElement("span");
                tag.className = "erd-rel-tag";
                tag.style.cssText = "color:var(--success,#10b981);background:color-mix(in srgb,var(--success,#10b981) 12%,transparent);";
                tag.title = `Referenced by: ${r.from_table}.${r.from_col}`;
                tag.textContent = `← ${r.from_table}.${r.from_col}`;
                relWrap.appendChild(tag);
            });
            card.appendChild(relWrap);
        }

        // ---- Column list ----
        const colList = document.createElement("div");
        colList.className = "erd-col-list";

        cols.forEach(col => {
            const isFk = !!fkCols[`${tbl}.${col.name}`];
            const colRow = document.createElement("div");
            colRow.className = "erd-col-row";
            colRow.dataset.colname = col.name.toLowerCase();
            colRow.title = `Klik untuk sisipkan "${col.name}" ke editor`;

            let badges = "";
            if (col.pk)             badges += `<span class="erd-badge erd-badge-pk">PK</span>`;
            if (isFk)               badges += `<span class="erd-badge erd-badge-fk">FK</span>`;
            if (!col.nullable && !col.pk) badges += `<span class="erd-badge erd-badge-nn">NN</span>`;

            colRow.innerHTML = `${badges}<span class="erd-col-name">${escapeHtml(col.name)}</span><span class="erd-col-type">${shortType(col.type)}</span>`;

            colRow.addEventListener("click", () => {
                if (editor) {
                    editor.replaceSelection(col.name);
                    editor.focus();
                }
                // Flash highlight
                colRow.classList.add("erd-highlight");
                setTimeout(() => colRow.classList.remove("erd-highlight"), 700);
            });

            colList.appendChild(colRow);
        });

        card.appendChild(colList);
        container.appendChild(card);
    });

    if (tableNames.length === 0) {
        container.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-muted);font-size:.82rem;">Database belum diinisialisasi. Minta admin untuk memuat data classicmodels.</div>';
    }
}

function shortType(pgType) {
    const map = {
        "character varying": "varchar",
        "double precision": "double",
        "integer": "int",
        "smallint": "int2",
        "bigint": "int8",
        "boolean": "bool",
        "timestamp without time zone": "timestamp",
        "timestamp with time zone": "timestamptz",
        "character": "char",
        "numeric": "numeric",
        "text": "text",
    };
    return map[pgType] || pgType;
}

function bindErdSearch() {
    const input = document.getElementById("erd-search");
    if (!input) return;
    input.addEventListener("input", () => {
        const q = input.value.trim().toLowerCase();
        const cards = document.querySelectorAll(".erd-table-card");
        cards.forEach(card => {
            const tbl = (card.dataset.table || "").toLowerCase();
            const colRows = card.querySelectorAll(".erd-col-row");

            if (!q) {
                card.classList.remove("erd-hidden");
                colRows.forEach(r => r.classList.remove("erd-highlight"));
                return;
            }

            // Check table name match
            const tblMatch = tbl.includes(q);
            // Check column name match
            let colMatch = false;
            colRows.forEach(r => {
                const cn = (r.dataset.colname || "");
                if (cn.includes(q)) {
                    r.classList.add("erd-highlight");
                    colMatch = true;
                } else {
                    r.classList.remove("erd-highlight");
                }
            });

            if (tblMatch || colMatch) {
                card.classList.remove("erd-hidden");
                // Auto-open if column matched
                if (colMatch && !tblMatch) card.classList.add("open");
            } else {
                card.classList.add("erd-hidden");
            }
        });
    });
}

function bindErdRelToggle() {
    const btn = document.getElementById("erd-toggle-rel");
    const legend = document.getElementById("erd-rel-legend");
    if (!btn) return;
    btn.addEventListener("click", () => {
        _erdShowRel = !_erdShowRel;
        btn.style.color = _erdShowRel ? "var(--accent)" : "";
        btn.style.borderColor = _erdShowRel ? "var(--accent)" : "";
        if (legend) legend.style.display = _erdShowRel ? "flex" : "none";
        document.querySelectorAll(".erd-rel-section").forEach(el => {
            el.style.display = _erdShowRel ? "flex" : "none";
        });
    });
}

// ==============================================================================
// 8. Utilities
// ==============================================================================
function formatQuery() {
    // Simple basic keyword uppercase formatting
    if (!editor) return;
    let text = editor.getValue();
    const keywords = [
        "select", "from", "where", "group by", "order by", "having", 
        "inner join", "left join", "right join", "join", "on", 
        "as", "count", "sum", "avg", "min", "max", "round", "concat",
        "distinct", "limit", "offset", "and", "or", "in", "like", "between",
        "is null", "is not null", "asc", "desc"
    ];
    keywords.forEach(kw => {
        const reg = new RegExp(`\\b${kw}\\b`, "gi");
        text = text.replace(reg, kw.toUpperCase());
    });
    editor.setValue(text);
}

function showToast(message, type = "success") {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        container.className = "toast-container";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 200);
    }, 4000);
}

function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatMarkdown(text) {
    if (!text) return "";
    let safe = escapeHtml(text);
    safe = safe.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/`(.*?)`/g, "<code>$1</code>");
    return safe.split("\n\n").map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

// ==============================================================================
// ERD Diagram — Canvas Renderer (Force-Directed Layout, Pan/Zoom)
// ==============================================================================

const ERD_TBL_W      = 215;
const ERD_COL_H      = 21;
const ERD_HDR_H      = 32;
const ERD_PAD        = 8;
const ERD_PALETTE    = [
    '#6366f1','#8b5cf6','#ec4899','#14b8a6','#f59e0b',
    '#ef4444','#3b82f6','#10b981','#f97316','#06b6d4'
];

let _dc = null;   // canvas
let _dx = null;   // ctx
let _dn = [];     // nodes
let _de = [];     // edges
let _dt = {s:1, px:0, py:0}; // transform
let _dDrag = false;
let _dLast = {x:0,y:0};
let _dSelected = null; // highlighted table name

function openErdDiagram() {
    const modal = document.getElementById('erd-diagram-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    _initDiagram();
}

function closeErdDiagram() {
    const modal = document.getElementById('erd-diagram-modal');
    if (modal) modal.style.display = 'none';
    _dSelected = null;
}

function _initDiagram() {
    const canvas = document.getElementById('erd-diagram-canvas');
    if (!canvas) return;
    const wrap = document.getElementById('erd-canvas-wrapper');
    if (wrap) {
        canvas.width  = wrap.clientWidth  || window.innerWidth  - 40;
        canvas.height = wrap.clientHeight || window.innerHeight - 130;
    }
    _dc = canvas;
    _dx = canvas.getContext('2d');
    _dn = [];
    _dSelected = null;

    const schema = _erdSchemaCache || {};
    const rels   = _erdRelCache   || [];
    _de = rels;

    if (Object.keys(schema).length === 0) {
        loadErdSchema().then(() => _initDiagram());
        return;
    }

    // Build FK lookup
    const fkCols = {};
    rels.forEach(r => { fkCols[`${r.from_table}.${r.from_col}`] = true; });

    const tables = Object.keys(schema).sort();
    const cols_n = Math.ceil(Math.sqrt(tables.length));
    const spacingX = 280, spacingY = 320;

    tables.forEach((tbl, i) => {
        const cols = schema[tbl];
        const h = ERD_HDR_H + cols.length * ERD_COL_H + ERD_PAD;
        _dn.push({
            table: tbl,
            cols,
            fkCols,
            w: ERD_TBL_W,
            h,
            x: (i % cols_n) * spacingX + 50,
            y: Math.floor(i / cols_n) * spacingY + 50,
            vx: 0, vy: 0,
            color: ERD_PALETTE[i % ERD_PALETTE.length]
        });
    });

    _runLayout(320);
    _centerDiagram();
    _drawDiagram();
    _bindDiagramEvents();
}

function _runLayout(iter) {
    const REP  = 20000;
    const SLEN = 270;
    const SK   = 0.18;
    const DAMP = 0.82;
    const ns   = _dn;

    for (let it = 0; it < iter; it++) {
        ns.forEach(n => { n.fx = 0; n.fy = 0; });

        // Repulsion
        for (let i = 0; i < ns.length; i++) {
            for (let j = i + 1; j < ns.length; j++) {
                const a = ns[i], b = ns[j];
                const dx = (b.x + b.w/2) - (a.x + a.w/2);
                const dy = (b.y + b.h/2) - (a.y + a.h/2);
                const d  = Math.sqrt(dx*dx + dy*dy) || 1;
                const f  = REP / (d*d);
                a.fx -= f*dx/d; a.fy -= f*dy/d;
                b.fx += f*dx/d; b.fy += f*dy/d;
            }
        }

        // Spring along edges
        _de.forEach(e => {
            const a = ns.find(n => n.table === e.from_table);
            const b = ns.find(n => n.table === e.to_table);
            if (!a || !b || a === b) return;
            const dx = (b.x + b.w/2) - (a.x + a.w/2);
            const dy = (b.y + b.h/2) - (a.y + a.h/2);
            const d  = Math.sqrt(dx*dx + dy*dy) || 1;
            const f  = SK * (d - SLEN);
            a.fx += f*dx/d; a.fy += f*dy/d;
            b.fx -= f*dx/d; b.fy -= f*dy/d;
        });

        ns.forEach(n => {
            n.vx = (n.vx + n.fx) * DAMP;
            n.vy = (n.vy + n.fy) * DAMP;
            n.x  += n.vx;
            n.y  += n.vy;
        });
    }
}

function _centerDiagram() {
    if (!_dc || _dn.length === 0) return;
    let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
    _dn.forEach(n => {
        minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x + n.w); maxY = Math.max(maxY, n.y + n.h);
    });
    const gW = maxX - minX, gH = maxY - minY;
    const pad = 70;
    const sx = (_dc.width  - pad*2) / gW;
    const sy = (_dc.height - pad*2) / gH;
    _dt.s  = Math.min(sx, sy, 1.15);
    _dt.px = (_dc.width  - gW * _dt.s) / 2 - minX * _dt.s;
    _dt.py = (_dc.height - gH * _dt.s) / 2 - minY * _dt.s;
}

function _drawDiagram() {
    const ctx = _dx, cv = _dc;
    if (!ctx || !cv) return;
    ctx.clearRect(0, 0, cv.width, cv.height);

    // Canvas background
    ctx.fillStyle = _cssVar('--bg-app', '#16161e');
    ctx.fillRect(0, 0, cv.width, cv.height);

    // Faint grid dots
    ctx.fillStyle = 'rgba(255,255,255,0.04)';
    const gridS = 28;
    for (let gx = gridS/2; gx < cv.width; gx += gridS)
        for (let gy = gridS/2; gy < cv.height; gy += gridS) {
            ctx.beginPath();
            ctx.arc(gx, gy, 1, 0, Math.PI*2);
            ctx.fill();
        }

    ctx.save();
    ctx.translate(_dt.px, _dt.py);
    ctx.scale(_dt.s, _dt.s);

    _drawEdges(ctx);
    _dn.forEach(n => _drawNode(ctx, n));

    ctx.restore();
}

function _drawNode(ctx, n) {
    const {x, y, w, h, table, cols, fkCols, color} = n;
    const R = 7;
    const isSelected = _dSelected === table;

    // Card shadow
    ctx.shadowColor = isSelected ? color : 'rgba(0,0,0,0.5)';
    ctx.shadowBlur  = isSelected ? 22 : 14;
    ctx.shadowOffsetY = 4;

    // Body
    _rRect(ctx, x, y, w, h, R);
    ctx.fillStyle = _cssVar('--bg-surface', '#1e1e2e');
    ctx.fill();

    // Outline (highlight when selected)
    ctx.strokeStyle = isSelected ? color : _cssVar('--border-subtle', '#2a2a3e');
    ctx.lineWidth   = isSelected ? 2.5 : 1;
    _rRect(ctx, x, y, w, h, R); ctx.stroke();

    ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;

    // Header gradient
    const g = ctx.createLinearGradient(x, y, x + w, y + ERD_HDR_H);
    g.addColorStop(0, color);
    g.addColorStop(1, _lightenHex(color, 25));
    _rRectTop(ctx, x, y, w, ERD_HDR_H, R);
    ctx.fillStyle = g; ctx.fill();

    // Header separator
    ctx.strokeStyle = _darkenHex(color, 20);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, y + ERD_HDR_H); ctx.lineTo(x + w, y + ERD_HDR_H); ctx.stroke();

    // Table name
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 12.5px "Segoe UI",system-ui,sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(table, x + w/2, y + ERD_HDR_H/2);

    // Column rows
    const mainColor  = _cssVar('--text-main',  '#e4e4f0');
    const mutedColor = _cssVar('--text-muted', '#888898');
    const elevColor  = _cssVar('--bg-surface-elevated', 'rgba(255,255,255,0.04)');
    const sepColor   = _cssVar('--border-subtle', '#2a2a3e');

    cols.forEach((col, i) => {
        const rowY  = y + ERD_HDR_H + i * ERD_COL_H;
        const midY  = rowY + ERD_COL_H / 2 + 1;
        const isPk  = col.pk;
        const isFk  = !!(fkCols && fkCols[`${table}.${col.name}`]);

        // Alternating row tint
        if (i % 2 === 0) {
            ctx.fillStyle = elevColor;
            ctx.fillRect(x + 1, rowY, w - 2, ERD_COL_H);
        }

        // Row separator
        if (i < cols.length - 1) {
            ctx.strokeStyle = sepColor; ctx.lineWidth = 0.5;
            ctx.beginPath(); ctx.moveTo(x+1, rowY + ERD_COL_H); ctx.lineTo(x+w-1, rowY + ERD_COL_H); ctx.stroke();
        }

        // Badge(s)
        let bx = x + 5;
        if (isPk) { bx = _badge(ctx, bx, midY, 'PK', '#f59e0b'); }
        if (isFk) { bx = _badge(ctx, bx, midY, 'FK', '#6366f1'); }

        // Column name
        ctx.fillStyle = isPk ? '#fcd34d' : mainColor;
        ctx.font = isPk ? 'bold 11px "Segoe UI",system-ui' : '11px "Segoe UI",system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(col.name, bx + 3, midY);

        // Type label (right-aligned, truncated)
        const typeStr = _shortT(col.type);
        ctx.fillStyle = isFk ? '#818cf8' : mutedColor;
        ctx.font = '9.5px "Courier New",monospace';
        ctx.textAlign = 'right';
        ctx.fillText(typeStr, x + w - 6, midY);
    });

    ctx.textAlign = 'left';
}

function _badge(ctx, x, cy, label, color) {
    const pad = 3, fs = 8.5;
    ctx.font = `bold ${fs}px "Segoe UI",system-ui`;
    const tw = ctx.measureText(label).width;
    const bw = tw + pad * 2;
    const bh = 12;
    ctx.fillStyle = color + '33';
    ctx.strokeStyle = color;
    ctx.lineWidth = 0.8;
    _rRectFull(ctx, x, cy - bh/2, bw, bh, 2);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.fillText(label, x + bw/2, cy + 0.5);
    return x + bw + 4;
}

function _drawEdges(ctx) {
    const nMap = {};
    _dn.forEach(n => nMap[n.table] = n);

    // Group edges by table pair
    const grp = {};
    _de.forEach(e => {
        const key = `${e.from_table}||${e.to_table}`;
        if (!grp[key]) grp[key] = [];
        grp[key].push(e);
    });

    Object.values(grp).forEach(edges => {
        edges.forEach((e, idx) => {
            const a = nMap[e.from_table], b = nMap[e.to_table];
            if (!a || !b || a === b) return;
            const off = (idx - (edges.length - 1) / 2) * 12;
            _drawEdge(ctx, a, b, e, off);
        });
    });
}

function _drawEdge(ctx, a, b, edge, off) {
    const acx = a.x + a.w/2, bcx = b.x + b.w/2;
    const leftToRight = acx <= bcx;
    const ax = leftToRight ? a.x + a.w : a.x;
    const bx = leftToRight ? b.x        : b.x + b.w;

    const fromIdx = a.cols.findIndex(c => c.name === edge.from_col);
    const toIdx   = b.cols.findIndex(c => c.name === edge.to_col);
    const ay = a.y + ERD_HDR_H + (fromIdx >= 0 ? fromIdx * ERD_COL_H + ERD_COL_H/2 : a.h/2) + off;
    const by = b.y + ERD_HDR_H + (toIdx   >= 0 ? toIdx   * ERD_COL_H + ERD_COL_H/2 : b.h/2) + off;

    const cp = Math.min(Math.abs(bx - ax) * 0.5, 120);
    const cpx1 = ax + (leftToRight ?  cp : -cp);
    const cpx2 = bx + (leftToRight ? -cp :  cp);

    const isHighlighted = _dSelected &&
        (_dSelected === edge.from_table || _dSelected === edge.to_table);

    const baseColor  = isHighlighted ? '#818cf8' : 'rgba(99,102,241,0.45)';
    const arrowColor = isHighlighted ? '#6366f1' : 'rgba(99,102,241,0.75)';

    ctx.strokeStyle = baseColor;
    ctx.lineWidth   = isHighlighted ? 2 : 1.2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.bezierCurveTo(cpx1, ay, cpx2, by, bx, by);
    ctx.stroke();

    // Arrowhead on target (PK side)
    const dir = leftToRight ? 1 : -1;
    const AS = 7;
    ctx.fillStyle = arrowColor;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(bx - dir*AS, by - 4);
    ctx.lineTo(bx - dir*AS, by + 4);
    ctx.closePath();
    ctx.fill();

    // Dot on source (FK / many side)
    ctx.fillStyle = arrowColor;
    ctx.beginPath();
    ctx.arc(ax, ay, isHighlighted ? 4.5 : 3, 0, Math.PI*2);
    ctx.fill();
}

function _bindDiagramEvents() {
    const cv = _dc;
    if (!cv || cv._erdBound) return;
    cv._erdBound = true;

    // Pan
    cv.addEventListener('mousedown', e => {
        _dDrag = true;
        _dLast = {x: e.clientX, y: e.clientY};
        cv.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', e => {
        if (!_dDrag) return;
        _dt.px += e.clientX - _dLast.x;
        _dt.py += e.clientY - _dLast.y;
        _dLast = {x: e.clientX, y: e.clientY};
        _drawDiagram();
    });
    window.addEventListener('mouseup', () => { _dDrag = false; if (cv) cv.style.cursor = 'grab'; });

    // Zoom
    cv.addEventListener('wheel', e => {
        e.preventDefault();
        const d = e.deltaY > 0 ? 0.88 : 1.14;
        const rect = cv.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        _dt.px = mx - d*(mx - _dt.px);
        _dt.py = my - d*(my - _dt.py);
        _dt.s *= d;
        _drawDiagram();
    }, {passive:false});

    // Click to highlight table
    cv.addEventListener('click', e => {
        const rect = cv.getBoundingClientRect();
        const mx = (e.clientX - rect.left - _dt.px) / _dt.s;
        const my = (e.clientY - rect.top  - _dt.py) / _dt.s;
        let hit = null;
        _dn.forEach(n => {
            if (mx >= n.x && mx <= n.x + n.w && my >= n.y && my <= n.y + n.h) hit = n.table;
        });
        _dSelected = (hit === _dSelected) ? null : hit;
        _drawDiagram();
    });

    cv.style.cursor = 'grab';
}

function erdZoomIn()    { _dt.s *= 1.2; _drawDiagram(); }
function erdZoomOut()   { _dt.s /= 1.2; _drawDiagram(); }
function erdResetView() { _centerDiagram(); _dSelected = null; _drawDiagram(); }

// ---- Canvas helpers ----
function _rRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y); ctx.quadraticCurveTo(x+w,y,x+w,y+r);
    ctx.lineTo(x+w,y+h-r); ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
    ctx.lineTo(x+r,y+h); ctx.quadraticCurveTo(x,y+h,x,y+h-r);
    ctx.lineTo(x,y+r); ctx.quadraticCurveTo(x,y,x+r,y); ctx.closePath();
}
function _rRectTop(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y); ctx.quadraticCurveTo(x+w,y,x+w,y+r);
    ctx.lineTo(x+w,y+h); ctx.lineTo(x,y+h);
    ctx.lineTo(x,y+r); ctx.quadraticCurveTo(x,y,x+r,y); ctx.closePath();
}
function _rRectFull(ctx, x, y, w, h, r) { _rRect(ctx,x,y,w,h,r); }

function _cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function _lightenHex(hex, amt) {
    const n = parseInt(hex.slice(1),16);
    const r = Math.min(255,(n>>16)+amt);
    const g = Math.min(255,((n>>8)&0xff)+amt);
    const b = Math.min(255,(n&0xff)+amt);
    return '#'+(((r<<16)|(g<<8)|b).toString(16).padStart(6,'0'));
}
function _darkenHex(hex, amt) { return _lightenHex(hex, -amt); }

function _shortT(t) {
    const m = {
        'character varying':'varchar','double precision':'double','integer':'int',
        'smallint':'int2','bigint':'int8','boolean':'bool',
        'timestamp without time zone':'timestamp','timestamp with time zone':'timestamptz',
        'character':'char','numeric':'numeric'
    };
    return m[t] || (t && t.length > 9 ? t.slice(0,9)+'..' : t);
}
