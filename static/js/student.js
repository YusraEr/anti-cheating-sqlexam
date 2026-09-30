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

// Initialize when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
    studentNim = document.body.dataset.nim || "";
    studentSessionToken = document.body.dataset.token || "";

    initCodeMirror();
    setupAntiCheat();
    loadQuestions();
    loadSchema();
    startHeartbeat();

    // Bind Action Buttons
    document.getElementById("btn-run-query").addEventListener("click", runQuery);
    document.getElementById("btn-submit-answer").addEventListener("click", submitAnswer);
    document.getElementById("btn-format-sql")?.addEventListener("click", formatQuery);
});

// ==============================================================================
// 1. CodeMirror SQL Editor Initialization
// ==============================================================================
function initCodeMirror() {
    const textarea = document.getElementById("sql-editor");
    if (!textarea) return;

    editor = CodeMirror.fromTextArea(textarea, {
        mode: "text/x-pgsql",
        theme: "dracula",
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
        if (document.hidden) {
            showToast("Peringatan: Berpindah tab atau aplikasi dicatat oleh sistem!", "danger");
            logCheatingAction("blur_tab", "Mahasiswa memindahkan fokus tab atau keluar jendela ujian");
        } else {
            logCheatingAction("focus_tab", "Mahasiswa kembali ke jendela ujian");
        }
    });

    window.addEventListener("blur", () => {
        logCheatingAction("blur_tab", "Window blur terdeteksi");
    });
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
async function loadSchema() {
    const container = document.getElementById("schema-tree-container");
    if (!container) return;

    try {
        const res = await fetch("/api/student/schema");
        const data = await res.json();
        const schema = data.schema || {};

        container.innerHTML = "";
        Object.keys(schema).forEach(table => {
            const tableItem = document.createElement("div");
            tableItem.className = "schema-tree-item";
            tableItem.innerHTML = `<strong>📁 ${table}</strong>`;
            
            const columnsList = document.createElement("div");
            columnsList.className = "schema-columns-list";

            schema[table].forEach(col => {
                const colRow = document.createElement("div");
                colRow.className = "schema-col-row";
                const pkLabel = col.pk ? `<span class="schema-col-pk">🔑 PK</span>` : "";
                colRow.innerHTML = `
                    <span style="cursor: pointer;" title="Klik untuk masukkan nama kolom">${col.name} ${pkLabel}</span>
                    <span>${col.type}</span>
                `;
                // Clicking column name inserts it into SQL editor
                colRow.querySelector("span").addEventListener("click", (e) => {
                    e.stopPropagation();
                    editor.replaceSelection(col.name);
                    editor.focus();
                });
                columnsList.appendChild(colRow);
            });

            // Toggle table expand/collapse
            tableItem.addEventListener("click", () => {
                columnsList.classList.toggle("open");
            });

            container.appendChild(tableItem);
            container.appendChild(columnsList);
        });
    } catch (err) {
        container.innerHTML = `<div style="padding: 0.75rem; color: #ef4444; font-size: 0.8rem;">Gagal memuat skema database.</div>`;
    }
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
    // Bold: **text**
    safe = safe.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    // Code: `code`
    safe = safe.replace(/`(.*?)`/g, "<code>$1</code>");
    // Line breaks to paragraphs
    return safe.split("\n\n").map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}
