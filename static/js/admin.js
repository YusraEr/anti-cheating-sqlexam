const adminFetch = window.fetch.bind(window);
const adminReadVersions = new Map();
let lastAdminAccessWarning=-Infinity;
window.fetch = async function(input, options = {}) {
    const target = new URL(typeof input === "string" ? input : input.url, window.location.href);
    const method = (options.method || input.method || "GET").toUpperCase();
    if (target.origin === window.location.origin && target.pathname.startsWith("/api/admin/") && !["GET", "HEAD"].includes(method)) {
        const headers = new Headers(options.headers || input.headers);
        headers.set("X-CSRF-Token", document.querySelector('meta[name="admin-csrf-token"]')?.content || "");
        options = { ...options, headers };
    }
    const adminRequest=target.origin===window.location.origin && target.pathname.startsWith('/api/admin/');
    const readKey=adminRequest && method==='GET' ? target.pathname : null;
    const ticket=readKey ? (adminReadVersions.get(readKey)||0)+1 : 0;
    if (readKey) adminReadVersions.set(readKey,ticket);
    const response=await adminFetch(input, options);
    if (readKey && adminReadVersions.get(readKey)!==ticket) throw new Error('Respons lama diabaikan.');
    if (adminRequest && !response.ok) {
        const data=await (response.clone ? response.clone() : response).json().catch(()=>({}));
        if (response.status===403 && Date.now()-lastAdminAccessWarning>30000) {
            lastAdminAccessWarning=Date.now();
            showToast('Akses admin ditolak. Muat ulang atau login kembali.','warning');
            if (String(data.detail).toLowerCase().startsWith('akses ditolak') && typeof window.location.assign==='function') window.location.assign('/admin/login');
        }
        const message=typeof data.detail==='string' ? data.detail : 'Permintaan admin gagal.';
        const error=new Error(message);
        error.status=response.status;
        throw error;
    }
    return response;
};

/**

 * Offline SQL Exam System - Admin Dashboard Controller

 * Features:

 * - Real-Time Student Activity Monitoring & Tab Switch Alerting

 * - Question Management (CRUD Modal)

 * - Database Sandbox Reset & Custom SQL Import

 * - Submissions Export (CSV & ZIP of .sql files)

 * - Live Event Logs Tracking

 */



let activeTab = "monitoring";

let questionsList = [];

let packagesList = [];

let availableCategories = [];

let monitorInterval = null;
let examStartPending = false;
let examStateVersion = 0;
let examValidationPending = false;
let currentExamState = "waiting";
let examReadiness = {ready: false, message: "Belum divalidasi."};



// ==============================================================================

// Global Theme System

// ==============================================================================

const THEME_STORAGE_KEY = "exam_global_theme";



function initGlobalTheme() {

    const select = document.getElementById("global-theme-select");

    const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "dracula";

    applyGlobalTheme(savedTheme);



    if (select) {

        select.addEventListener("change", () => {

            applyGlobalTheme(select.value);

            localStorage.setItem(THEME_STORAGE_KEY, select.value);

        });

    }

}



function applyGlobalTheme(theme) {

    document.documentElement.setAttribute("data-theme", theme);

    const select = document.getElementById("global-theme-select");

    if (select) select.value = theme;

}





document.addEventListener("DOMContentLoaded", () => {

    initGlobalTheme();

    initAdminTabs();

    loadDashboardData();

    loadQuestionsData();

    loadSchemaPreview();

    loadLogsData();



    // Start auto refresh for student monitoring every 10 seconds

    monitorInterval = setInterval(() => {

        if (activeTab === "monitoring") {

            loadDashboardData();

        }

    }, 10000);



    // Initial load and periodic polling for Waiting Room monitor
loadWaitingRoomPanel();
// Gentle fallback polling every 20 seconds

    setInterval(loadWaitingRoomPanel, 20000);



    // Event Bindings

    document.getElementById("btn-refresh-monitor")?.addEventListener("click", loadDashboardData);

    document.getElementById("btn-exam-start")?.addEventListener("click", startExam);
    document.getElementById("btn-exam-validate")?.addEventListener("click", validateExamReadiness);

    document.getElementById("btn-exam-stop")?.addEventListener("click", stopExam);

    document.getElementById("btn-stop-all-sessions")?.addEventListener("click", confirmStopAllSessions);

    document.getElementById("btn-clear-sessions")?.addEventListener("click", openClearSessionsModal);

    document.getElementById("btn-confirm-clear-sessions")?.addEventListener("click", executeClearSessions);

    document.getElementById("btn-add-question")?.addEventListener("click", openAddQuestionModal);

    document.getElementById("btn-add-package")?.addEventListener("click", openAddPackageModal);

    document.getElementById("filter-question-category")?.addEventListener("change", () => {

        renderQuestionsTable();

        renderPackageCards();

    });

    document.getElementById("modal-q-category")?.addEventListener("change", updateNextQuestionNumber);
    document.getElementById("modal-q-grading-mode")?.addEventListener("change", updateQuestionKeyRequirement);

    document.getElementById("btn-delete-all-questions")?.addEventListener("click", openDeleteAllQuestionsModal);

    document.getElementById("btn-confirm-delete-all-questions")?.addEventListener("click", deleteAllQuestions);

    document.getElementById("delete-all-confirm-input")?.addEventListener("input", (e) => {

        const confirmBtn = document.getElementById("btn-confirm-delete-all-questions");

        if (confirmBtn) confirmBtn.disabled = e.target.value.trim() !== "HAPUS SEMUA";

    });

    document.getElementById("btn-reset-db")?.addEventListener("click", confirmResetDatabase);

    document.getElementById("admin-db-selector")?.addEventListener("change", (e) => {
        loadSchemaPreview(e.target.value);
    });

    document.getElementById("btn-load-table-data")?.addEventListener("click", () => {
        const tbl = document.getElementById("admin-table-data-select")?.value;
        if (tbl) loadTableData(tbl, 1);
    });

    document.getElementById("admin-table-data-select")?.addEventListener("change", (e) => {
        if (e.target.value) loadTableData(e.target.value, 1);
    });

    document.getElementById("admin-table-data-limit")?.addEventListener("change", () => {
        if (currentViewingTable) loadTableData(currentViewingTable, 1);
    });

    document.getElementById("btn-table-data-prev")?.addEventListener("click", () => {
        if (currentViewingTable && currentTableDataPage > 1) {
            loadTableData(currentViewingTable, currentTableDataPage - 1);
        }
    });

    document.getElementById("btn-table-data-next")?.addEventListener("click", () => {
        if (currentViewingTable) {
            loadTableData(currentViewingTable, currentTableDataPage + 1);
        }
    });

    document.getElementById("btn-refresh-logs")?.addEventListener("click", () => loadLogsData(logsCurrentPage));

    document.getElementById("btn-logs-prev")?.addEventListener("click", () => {
        if (logsCurrentPage > 1) {
            loadLogsData(logsCurrentPage - 1);
        }
    });

    document.getElementById("btn-logs-next")?.addEventListener("click", () => {
        loadLogsData(logsCurrentPage + 1);
    });

    document.getElementById("btn-clear-logs")?.addEventListener("click", confirmClearLogs);

    // Custom SQL Upload Form

    document.getElementById("sql-upload-form")?.addEventListener("submit", handleSqlUpload);



    // Change Master Password Modal & Form

    document.getElementById("btn-open-change-pw-modal")?.addEventListener("click", openChangePasswordModal);

    document.getElementById("form-change-password")?.addEventListener("submit", handleChangePassword);

});






function updateWaitingParticipantsList(participants) {

    const countEl = document.getElementById("admin-waiting-count");

    const namesEl = document.getElementById("admin-waiting-names");



    if (countEl) countEl.textContent = participants.length;



    if (namesEl) {

        namesEl.innerHTML = "";

        participants.slice(0, 20).forEach(p => {

            const pill = document.createElement("span");

            pill.style.cssText = "background:var(--bg-surface-elevated);border:1px solid var(--border-subtle);border-radius:999px;padding:0.15rem 0.55rem;font-size:0.72rem;color:var(--text-main);";

            pill.textContent = p.name;

            namesEl.appendChild(pill);

        });

        if (participants.length > 20) {

            const more = document.createElement("span");

            more.style.cssText = "font-size:0.72rem;color:var(--text-muted);";

            more.textContent = `+${participants.length - 20} lainnya`;

            namesEl.appendChild(more);

        }

    }

}



async function loadWaitingRoomPanel() {

    const version = examStateVersion;

    try {

        const res = await fetch("/api/admin/waiting/participants");

        if (!res.ok) return;

        const data = await res.json();

        if (version !== examStateVersion) return;

        const participants = data.participants || [];

        const examState = data.exam_state || "waiting";



        updateWaitingParticipantsList(participants);

        if (!examStartPending && !examValidationPending) {
            examReadiness = data.readiness || {ready: false, message: "Muat ulang server untuk memeriksa kesiapan."};
            updateExamStateBadge(examState);
        }

    } catch (_) {}

}



function updateExamStateBadge(state) {

    currentExamState = state;

    const badge = document.getElementById("admin-exam-state-badge");

    const startBtn = document.getElementById("btn-exam-start");

    const stopBtn = document.getElementById("btn-exam-stop");



    if (state === "active") {

        if (badge) {

            badge.textContent = "Aktif";

            badge.className = "status-badge badge-online";

        }

        if (startBtn) startBtn.disabled = true;

        if (stopBtn) stopBtn.disabled = false;

    } else {

        if (badge) {

            badge.textContent = state === "finished" ? "Selesai" : "Menunggu";

            badge.className = "status-badge badge-warning";

        }

        if (startBtn) startBtn.disabled = examStartPending || examValidationPending || !examReadiness.ready;

        if (stopBtn) stopBtn.disabled = true;

    }

    const validateBtn = document.getElementById("btn-exam-validate");
    if (validateBtn) validateBtn.disabled = state === "active" || examStartPending || examValidationPending;
    const readinessLabel = document.getElementById("admin-exam-readiness");
    if (readinessLabel) {
        readinessLabel.textContent = examValidationPending ? "Validasi sedang berjalan. Peserta tetap menunggu."
            : examReadiness.ready ? `Siap (${examReadiness.question_count} soal).` : examReadiness.message;
    }
}


async function validateExamReadiness() {
    if (examValidationPending || examStartPending || currentExamState === "active") return;
    examValidationPending = true;
    examStateVersion += 1;
    examReadiness = {ready: false, message: "Belum divalidasi."};
    const button = document.getElementById("btn-exam-validate");
    if (button) button.textContent = "Memvalidasi soal...";
    updateExamStateBadge(currentExamState);
    try {
        const response = await fetch("/api/admin/exam/validate", {method: "POST"});
        const data = await response.json();
        examReadiness = data.readiness;
        showToast(data.message, "success");
    } catch (error) {
        examReadiness = {ready: false, message: error.message || "Validasi kesiapan gagal."};
        showToast(examReadiness.message, error.status >= 400 && error.status < 500 ? "warning" : "danger");
    } finally {
        examValidationPending = false;
        examStateVersion += 1;
        if (button) button.textContent = "Validasi Kesiapan Ujian";
        updateExamStateBadge(currentExamState);
        loadWaitingRoomPanel();
    }
}


async function startExam() {

    if (examStartPending || examValidationPending) return;
    examStartPending = true;
    examStateVersion += 1;

    const startBtn = document.getElementById("btn-exam-start");
    const startLabel = startBtn?.textContent || "Mulai Ujian";

    if (startBtn) {
        startBtn.disabled = true;
        startBtn.textContent = "Memulai ujian...";
    }

    try {

        const res = await fetch("/api/admin/exam/start", { method: "POST" });

        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || "Permintaan ditolak.");
        showToast(data.message || "Ujian dimulai.", "success");

        updateExamStateBadge("active");

    } catch (error) {

        showToast(error.message || "Gagal memulai ujian.", error.status >= 400 && error.status < 500 ? "warning" : "danger");

        if (startBtn) startBtn.disabled = false;

    } finally {
        examStartPending = false;
        examStateVersion += 1;
        if (startBtn) startBtn.textContent = startLabel;
        loadWaitingRoomPanel();
    }

}



async function stopExam() {

    if (!confirm("Hentikan ujian? Peserta yang sedang mengerjakan akan dikembalikan ke waiting room.")) return;
    examStateVersion += 1;

    const stopBtn = document.getElementById("btn-exam-stop");

    if (stopBtn) stopBtn.disabled = true;

    try {

        const res = await fetch("/api/admin/exam/stop", { method: "POST" });

        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || "Permintaan ditolak.");
        showToast(data.message || "Ujian dihentikan.", "success");

        updateExamStateBadge(data.state || "finished");

    } catch (_) {

        showToast("Gagal menghentikan ujian.", "danger");

        if (stopBtn) stopBtn.disabled = false;

    } finally {
        examStateVersion += 1;
        loadWaitingRoomPanel();
    }

}






// ==============================================================================

// 1. Navigation Tabs

// ==============================================================================

function initAdminTabs() {

    const tabButtons = document.querySelectorAll(".admin-tab-btn");

    tabButtons.forEach(btn => {

        btn.addEventListener("click", () => {

            tabButtons.forEach(b => b.classList.remove("active"));

            btn.classList.add("active");

            activeTab = btn.dataset.tab;



            document.querySelectorAll(".tab-pane").forEach(pane => {

                pane.style.display = pane.id === `tab-${activeTab}` ? "block" : "none";

            });



            if (activeTab === "monitoring") loadDashboardData();

            if (activeTab === "questions") loadQuestionsData();

            if (activeTab === "database") loadSchemaPreview();

            if (activeTab === "logs") loadLogsData();

        });

    });

}



// ==============================================================================

// 2. Live Student Monitoring

// ==============================================================================

async function loadDashboardData() {

    try {

        const res = await fetch("/api/admin/students");

        const data = await res.json();

        const students = data.students || [];



        // Update Stat Cards

        const total = students.length;

        const online = students.filter(s => s.is_active).length;

        const totalSubmissions = students.reduce((acc, s) => acc + (s.total_submitted || 0), 0);

        const suspiciousCount = students.filter(s => s.tab_switches > 0).length;



        document.getElementById("stat-total-students").textContent = total;

        document.getElementById("stat-online-students").textContent = online;

        document.getElementById("stat-total-submissions").textContent = totalSubmissions;

        document.getElementById("stat-suspicious-count").textContent = suspiciousCount;



        // Render Table Rows

        const tbody = document.getElementById("student-table-body");

        if (!tbody) return;



        if (students.length === 0) {

            tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 2rem;">Belum ada mahasiswa yang login.</td></tr>`;

            return;

        }



        let html = "";

        students.forEach(s => {

            const statusBadge = s.is_active

                ? `<span class="status-badge badge-online">Aktif</span>`

                : `<span class="status-badge badge-offline">Offline</span>`;



            let cheatBadge = `<span style="color: #64748b;">0</span>`;

            if (s.tab_switches > 0) {

                cheatBadge = `<span class="status-badge badge-warning" title="${s.tab_switches} kali berpindah tab/jendela">${s.tab_switches}x Pindah Tab</span>`;

            }



            const pkgBadge = s.selected_category

                ? `<span style="background: rgba(99, 102, 241, 0.15); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); font-weight: 700; padding: 3px 8px; border-radius: 4px; font-size: 0.78rem; white-space: nowrap; display: inline-block;">Paket ${escapeHtml(s.selected_category)}</span>`

                : `<span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); font-weight: 600; padding: 3px 8px; border-radius: 4px; font-size: 0.78rem; white-space: nowrap; display: inline-block;">Belum Pilih</span>`;



            const scoreVal = s.total_score || 0;
            const maxScore = s.max_score ?? 0;
            const scorePct = maxScore > 0 ? (scoreVal / maxScore * 100) : 0;
            const scoreColor = scorePct >= 80 ? "#10b981" : (scoreVal > 0 ? "#f59e0b" : "#94a3b8");
            const scoreBadge = `<div style="display: inline-flex; align-items: baseline; gap: 4px; font-weight: 700; white-space: nowrap;">
                <span style="color: ${scoreColor}; font-size: 0.95rem;">${scoreVal}</span>
                <span style="font-size: 0.72rem; color: var(--text-muted); font-weight: 500;">/ ${maxScore}</span>
                <span style="font-size: 0.72rem; color: #10b981; font-weight: 600; margin-left: 2px;">(${s.correct_count || 0} Benar)</span>
            </div>`;



            html += `

                <tr>

                    <td><strong>${escapeHtml(s.nim)}</strong></td>

                    <td>${escapeHtml(s.name)}</td>

                    <td><code>${escapeHtml(s.ip_address || "-")}</code></td>

                    <td>${pkgBadge}</td>

                    <td>${statusBadge}</td>

                    <td><strong style="color: #60a5fa;">${s.total_submitted}</strong> Soal</td>

                    <td>${scoreBadge}</td>

                    <td>${cheatBadge}</td>

                    <td style="text-align: right; white-space: nowrap;">
                        <button type="button" class="btn btn-secondary btn-sm" data-admin-action="unlock" data-nim="${escapeHtml(s.nim)}" data-name="${escapeHtml(s.name)}" title="Bebaskan kunci sesi perangkat agar mahasiswa dapat login kembali tanpa kehilangan data">
                            Bebaskan Sesi
                        </button>
                        <button type="button" class="btn btn-warning btn-sm" style="margin-left: 0.35rem;" data-admin-action="reset-package" data-nim="${escapeHtml(s.nim)}" data-name="${escapeHtml(s.name)}" title="Reset pilihan paket soal mahasiswa agar dapat memilih ulang">
                            Reset Paket
                        </button>
                    </td>

                </tr>

            `;

        });

        tbody.innerHTML = html;

    } catch (err) {

        console.error("Failed loading students:", err);

    }

}



async function resetStudentCategory(nim, name) {

    if (!confirm(`Apakah Anda yakin ingin me-reset paket soal untuk mahasiswa ${name} (${nim})?\nMahasiswa akan diminta memilih paket soal kembali saat membuka ujian.`)) return;

    try {

        const res = await fetch(`/api/admin/student/${encodeURIComponent(nim)}/reset-category`, {

            method: "POST",

            headers: { "Content-Type": "application/json" }

        });

        const data = await res.json();

        if (data.status === "success") {

            showToast(data.message || `Paket soal mahasiswa ${nim} berhasil direset.`);

            loadDashboardData();

        } else {

            showToast(data.message || "Gagal mereset paket soal.", "danger");

        }

    } catch (err) {

        console.error("Reset category error:", err);

        showToast(err.status ? err.message : "Gagal menghubungi server untuk reset paket soal.", "danger");

    }

}



async function clearStudentSessionLock(nim, name) {
    const studentLabel = name ? `${name} (${nim})` : nim;
    if (!confirm(`Bebaskan kunci sesi perangkat untuk mahasiswa ${studentLabel}?\n\nMahasiswa dapat langsung login kembali (misal jika perangkat restart atau ganti laptop) tanpa kehilangan jawaban yang telah disubmit.`)) return;

    try {
        const res = await fetch("/api/admin/clear-session-lock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nim: nim })
        });
        const data = await res.json();
        if (res.ok && data.status === "success") {
            showToast(data.message || `Kunci sesi untuk NIM ${nim} berhasil dibebaskan.`);
            loadDashboardData();
        } else {
            showToast(data.detail || data.message || "Gagal membebaskan kunci sesi mahasiswa.", "danger");
        }
    } catch (err) {
        showToast(err.status ? err.message : "Gagal membebaskan sesi mahasiswa.", "danger");
    }
}

async function kickStudent(nim) {
    return clearStudentSessionLock(nim);
}



async function confirmStopAllSessions() {

    if (!confirm("Hentikan SEMUA sesi mahasiswa yang aktif sekarang? Semua mahasiswa akan dipaksa logout dan perlu login ulang.")) return;

    const btn = document.getElementById("btn-stop-all-sessions");

    btn.disabled = true;

    btn.textContent = "Menghentikan...";

    try {

        const res = await fetch("/api/admin/stop-all-sessions", { method: "POST" });

        const data = await res.json();

        showToast(data.message || "Semua sesi telah dihentikan.");

        loadDashboardData();

    } catch (err) {

        showToast(err.status ? err.message : "Gagal menghentikan semua sesi.", "danger");

    } finally {

        btn.disabled = false;

        btn.textContent = "Hentikan Semua";

    }

}



function openClearSessionsModal() {

    document.getElementById("chk-clear-logs-also").checked = false;

    document.getElementById("clear-sessions-modal").style.display = "flex";

}



async function executeClearSessions() {

    const clearLogsAlso = document.getElementById("chk-clear-logs-also").checked;

    const btn = document.getElementById("btn-confirm-clear-sessions");

    btn.disabled = true;

    btn.textContent = "Membersihkan...";

    try {

        const res = await fetch("/api/admin/clear-sessions", {

            method: "POST",

            headers: { "Content-Type": "application/json" },

            body: JSON.stringify({ clear_logs: clearLogsAlso })

        });

        const data = await res.json();

        document.getElementById("clear-sessions-modal").style.display = "none";

        showToast(data.message || "Sesi sebelumnya berhasil dibersihkan.");

        loadDashboardData();

        if (clearLogsAlso) loadLogsData();

    } catch (err) {

        showToast(err.status ? err.message : "Gagal membersihkan sesi.", "danger");

    } finally {

        btn.disabled = false;

        btn.textContent = "Ya, Bersihkan Sekarang";

    }

}



async function confirmClearLogs() {

    if (!confirm("Hapus SELURUH riwayat log aktivitas ujian? Tindakan ini tidak dapat dibatalkan.")) return;

    const btn = document.getElementById("btn-clear-logs");

    btn.disabled = true;

    btn.textContent = "Membersihkan...";

    try {

        const res = await fetch("/api/admin/clear-logs", { method: "POST" });

        const data = await res.json();

        showToast(data.message || "Log berhasil dibersihkan.");

        loadLogsData();

    } catch (err) {

        showToast(err.status ? err.message : "Gagal membersihkan log.", "danger");

    } finally {

        btn.disabled = false;

        btn.textContent = "Bersihkan Log";

    }

}



// ==============================================================================

// 3. Question & Package Management (CRUD)

// ==============================================================================



async function loadQuestionsData() {

    try {

        const res = await fetch("/api/admin/questions");

        const data = await res.json();

        questionsList = data.questions || [];

        availableCategories = data.categories || [];

        packagesList = data.packages || [];



        // If packages not provided in questions endpoint response, fetch from /api/admin/packages

        if (!packagesList || packagesList.length === 0) {

            try {

                const pkgRes = await fetch("/api/admin/packages");

                const pkgData = await pkgRes.json();

                packagesList = pkgData.packages || [];

            } catch (pkgErr) {

                console.error("Failed fetching packages:", pkgErr);

            }

        }



        // Update category filter dropdown

        const filterSelect = document.getElementById("filter-question-category");

        if (filterSelect) {

            const currentFilter = filterSelect.value;

            let optionsHtml = '<option value="">Semua Paket</option>';



            if (packagesList && packagesList.length > 0) {

                packagesList.forEach(p => {

                    optionsHtml += `<option value="${escapeHtml(p.code)}">Paket ${escapeHtml(p.code)} - ${escapeHtml(p.name)} (${p.total_questions ?? p.question_count ?? 0} Soal)</option>`;

                });

            } else {

                const knownCats = new Set(["A", "B", "C", "D"]);

                if (availableCategories.length > 0) {

                    availableCategories.forEach(c => knownCats.add(c.category));

                }

                questionsList.forEach(q => {

                    if (q.category) knownCats.add(q.category);

                });

                Array.from(knownCats).sort().forEach(cat => {

                    const count = questionsList.filter(q => (q.category || "A") === cat).length;

                    optionsHtml += `<option value="${escapeHtml(cat)}">Paket ${escapeHtml(cat)} (${count} Soal)</option>`;

                });

            }



            filterSelect.innerHTML = optionsHtml;

            if (currentFilter && (packagesList.some(p => p.code === currentFilter) || currentFilter === "")) {

                filterSelect.value = currentFilter;

            }

        }



        // Update modal question category dropdown

        const modalCatSelect = document.getElementById("modal-q-category");

        if (modalCatSelect && packagesList && packagesList.length > 0) {

            const currentVal = modalCatSelect.value;

            modalCatSelect.innerHTML = packagesList.map(p => `

                <option value="${escapeHtml(p.code)}">Paket ${escapeHtml(p.code)} - ${escapeHtml(p.name)}</option>

            `).join("");

            if (currentVal && packagesList.some(p => p.code === currentVal)) {

                modalCatSelect.value = currentVal;

            }

        }



        renderPackageCards();

        renderQuestionsTable();

    } catch (err) {

        console.error("Failed loading questions:", err);

    }

}



function renderPackageCards() {

    const grid = document.getElementById("package-summary-grid");

    if (!grid) return;



    if (!packagesList || packagesList.length === 0) {

        grid.innerHTML = `

            <div style="grid-column: 1 / -1; padding: 1.25rem; text-align: center; color: var(--text-muted); background: var(--bg-surface); border: 1px dashed var(--border-subtle); border-radius: var(--radius-md);">

                Belum ada paket soal yang terdaftar. Klik <strong>+ Tambah Paket Baru</strong> untuk membuatnya.

            </div>

        `;

        return;

    }



    const activeFilter = document.getElementById("filter-question-category")?.value || "";



    let html = "";

    packagesList.forEach(p => {

        const isFiltered = activeFilter === p.code;

        const cardBorder = isFiltered ? "border-color: #6366f1; box-shadow: 0 0 0 1px #6366f1;" : "";

        const statusBadge = p.is_active

            ? `<span class="status-badge badge-online" style="font-size: 0.68rem; padding: 1px 6px;">Aktif</span>`

            : `<span class="status-badge badge-offline" style="font-size: 0.68rem; padding: 1px 6px;">Nonaktif</span>`;



        html += `

            <div class="stat-card" style="padding: 0.95rem 1rem; position: relative; ${cardBorder} transition: all 0.15s ease;">

                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.45rem;">

                    <div style="display: flex; align-items: center; gap: 0.4rem;">

                        <span style="background: rgba(99, 102, 241, 0.15); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); font-weight: 800; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem;">

                            Paket ${escapeHtml(p.code)}

                        </span>

                        ${statusBadge}

                    </div>

                    <div style="display: flex; gap: 0.3rem;">

                        <button type="button" class="btn btn-secondary btn-sm" data-admin-action="edit-package" data-code="${escapeHtml(p.code)}" title="Edit detail paket" style="padding: 2px 7px; font-size: 0.72rem; line-height: 1;">

                            Edit

                        </button>

                        <button type="button" class="btn btn-danger btn-sm" data-admin-action="delete-package" data-code="${escapeHtml(p.code)}" data-questions="${p.total_questions ?? p.question_count ?? 0}" data-students="${p.total_students ?? p.student_count ?? 0}" title="Hapus paket soal ini" style="padding: 2px 7px; font-size: 0.72rem; line-height: 1;">

                            Hapus

                        </button>

                    </div>

                </div>

                <div style="font-weight: 700; font-size: 0.92rem; color: var(--text-main); margin-bottom: 0.35rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(p.name)}">

                    ${escapeHtml(p.name)}

                </div>

                ${p.description ? `<div style="font-size: 0.76rem; color: var(--text-muted); margin-bottom: 0.6rem; max-height: 2.4em; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;" title="${escapeHtml(p.description)}">${escapeHtml(p.description)}</div>` : `<div style="font-size: 0.76rem; color: var(--text-subtle); margin-bottom: 0.6rem; font-style: italic;">Tidak ada deskripsi tambahan.</div>`}



                <div style="display: flex; justify-content: space-between; align-items: center; padding-top: 0.5rem; border-top: 1px solid var(--border-subtle); font-size: 0.76rem;">

                    <div style="display: flex; gap: 0.55rem; color: var(--text-muted);">

                        <span><strong style="color: #60a5fa;">${p.total_questions ?? p.question_count ?? 0}</strong> Soal</span>

                        <span>&bull;</span>

                        <span><strong style="color: #34d399;">${p.total_weight ?? p.total_score ?? 0}</strong> Poin</span>

                        <span>&bull;</span>

                        <span><strong style="color: #fbbf24;">${p.total_students ?? p.student_count ?? 0}</strong> Mhs</span>

                    </div>

                    <button type="button" class="btn btn-secondary btn-sm" data-admin-action="filter-package" data-code="${escapeHtml(p.code)}" style="padding: 2px 8px; font-size: 0.72rem; ${isFiltered ? 'background: #6366f1; color: #fff; border-color: #6366f1;' : ''}">

                        ${isFiltered ? 'Menampilkan' : 'Filter'}

                    </button>

                </div>

            </div>

        `;

    });

    grid.innerHTML = html;

}



function filterByPackage(code) {

    const filterSelect = document.getElementById("filter-question-category");

    if (!filterSelect) return;

    if (filterSelect.value === code) {

        filterSelect.value = "";

    } else {

        filterSelect.value = code;

    }

    renderQuestionsTable();

    renderPackageCards();

}



function openAddPackageModal() {

    const modal = document.getElementById("package-modal");

    if (!modal) return;

    document.getElementById("package-modal-title").textContent = "Tambah Paket Soal Baru";

    const codeInput = document.getElementById("modal-pkg-code");

    codeInput.value = "";

    codeInput.readOnly = false;

    codeInput.style.opacity = "1";

    document.getElementById("modal-pkg-name").value = "";

    document.getElementById("modal-pkg-desc").value = "";

    document.getElementById("modal-pkg-active").checked = true;

    modal.dataset.mode = "create";

    modal.style.display = "flex";

    setTimeout(() => codeInput.focus(), 50);

}



function openEditPackageModal(code) {

    const pkg = packagesList.find(p => p.code === code);

    if (!pkg) return;

    const modal = document.getElementById("package-modal");

    if (!modal) return;

    document.getElementById("package-modal-title").textContent = `Edit Paket Soal ${pkg.code}`;

    const codeInput = document.getElementById("modal-pkg-code");

    codeInput.value = pkg.code;

    codeInput.readOnly = true;

    codeInput.style.opacity = "0.7";

    document.getElementById("modal-pkg-name").value = pkg.name;

    document.getElementById("modal-pkg-desc").value = pkg.description || "";

    document.getElementById("modal-pkg-active").checked = Boolean(pkg.is_active);

    modal.dataset.mode = "edit";
    modal.dataset.editRevision = pkg.edit_revision;

    modal.style.display = "flex";

    setTimeout(() => document.getElementById("modal-pkg-name").focus(), 50);

}



function closePackageModal() {

    const modal = document.getElementById("package-modal");

    if (modal) modal.style.display = "none";

}



async function savePackageForm(e) {

    e.preventDefault();

    const modal = document.getElementById("package-modal");

    const mode = modal ? modal.dataset.mode : "create";

    const code = document.getElementById("modal-pkg-code").value.trim().toUpperCase();

    const name = document.getElementById("modal-pkg-name").value.trim();

    const description = document.getElementById("modal-pkg-desc").value.trim();

    const is_active = document.getElementById("modal-pkg-active").checked;



    if (!code || !name) {

        showToast("Kode dan Nama Paket wajib diisi!", "danger");

        return;

    }



    const saveBtn = document.getElementById("btn-save-package");

    if (saveBtn) {

        saveBtn.disabled = true;

        saveBtn.textContent = "Menyimpan...";

    }



    try {

        let res;

        if (mode === "edit") {

            res = await fetch(`/api/admin/packages/${encodeURIComponent(code)}`, {

                method: "PUT",

                headers: { "Content-Type": "application/json" },

                body: JSON.stringify({ name, description, is_active, expected_edit_revision: modal.dataset.editRevision })

            });

        } else {

            res = await fetch("/api/admin/packages", {

                method: "POST",

                headers: { "Content-Type": "application/json" },

                body: JSON.stringify({ code, name, description, is_active })

            });

        }



        const data = await res.json();

        if (res.ok && data.status === "success") {

            showToast(data.message || "Paket soal berhasil disimpan.");

            closePackageModal();

            await loadQuestionsData();

        } else {

            const errorMsg = data.detail || data.message || "Gagal menyimpan paket soal.";

            showToast(errorMsg, "danger");

        }

    } catch (err) {

        console.error("Save package error:", err);

        showToast(err.message || "Terjadi kesalahan jaringan saat menyimpan paket soal.", "danger");

    } finally {

        if (saveBtn) {

            saveBtn.disabled = false;

            saveBtn.textContent = "Simpan Paket";

        }

    }

}



async function confirmDeletePackage(code, totalQuestions, totalStudents) {

    if (totalStudents > 0) {

        alert(`Tidak dapat menghapus Paket ${code}:\nMasih ada ${totalStudents} mahasiswa yang terkunci pada paket ini.\nSilakan lakukan 'Reset Paket' pada mahasiswa terkait di tab Monitoring terlebih dahulu.`);

        return;

    }



    let deleteQuestions = false;

    if (totalQuestions > 0) {

        const confirmCascade = confirm(`PERINGATAN:\nPaket '${code}' saat ini memiliki ${totalQuestions} butir soal.\n\nApakah Anda yakin ingin menghapus Paket '${code}' sekaligus MENGHAPUS SEMUA ${totalQuestions} SOAL & SUBMISI di dalamnya?\n\nKlik OK untuk menghapus paket dan seluruh soalnya, atau Cancel untuk membatalkan.`);

        if (!confirmCascade) return;

        deleteQuestions = true;

    } else {

        if (!confirm(`Hapus Paket '${code}' dari daftar paket soal ujian?`)) return;

    }



    try {

        const revision=packagesList.find(p=>p.code===code)?.edit_revision || '';
        const url = `/api/admin/packages/${encodeURIComponent(code)}?delete_questions=${deleteQuestions}&expected_edit_revision=${encodeURIComponent(revision)}`;

        const res = await fetch(url, { method: "DELETE" });

        const data = await res.json();

        if (res.ok && data.status === "success") {

            showToast(data.message || `Paket ${code} berhasil dihapus.`);

            // If current filter was this package, reset filter

            const filterSelect = document.getElementById("filter-question-category");

            if (filterSelect && filterSelect.value === code) {

                filterSelect.value = "";

            }

            await loadQuestionsData();

        } else {

            showToast(data.detail || data.message || "Gagal menghapus paket soal.", "danger");

        }

    } catch (err) {

        console.error("Delete package error:", err);

        showToast(err.status ? err.message : "Gagal menghubungi server untuk menghapus paket soal.", "danger");

    }

}



function renderQuestionsTable() {

    const tbody = document.getElementById("questions-table-body");

    if (!tbody) return;



    const filterSelect = document.getElementById("filter-question-category");

    const activeFilter = filterSelect ? filterSelect.value : "";



    const displayed = activeFilter

        ? questionsList.filter(q => (q.category || "A") === activeFilter)

        : questionsList;



    if (displayed.length === 0) {

        const msg = activeFilter

            ? `Belum ada soal ujian untuk Paket ${escapeHtml(activeFilter)}.`

            : "Belum ada soal ujian yang dibuat.";

        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">${msg}</td></tr>`;

        return;

    }



    let html = "";

    displayed.forEach(q => {

        const cat = q.category || "A";

        html += `

            <tr>

                <td style="width: 100px; white-space: nowrap;">

                    <span style="background: rgba(99, 102, 241, 0.15); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); font-weight: 700; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; white-space: nowrap; display: inline-block;">

                        Paket ${escapeHtml(cat)}

                    </span>

                </td>

                <td style="width: 70px; white-space: nowrap;"><strong>#${q.question_number}</strong></td>

                <td style="min-width: 220px; max-width: 320px; white-space: normal; line-height: 1.4; word-break: break-word;">

                    <strong>${escapeHtml(q.title)}</strong>

                </td>

                <td style="width: 100px; white-space: nowrap;">

                    <span class="weight-pill" style="display: inline-block !important; white-space: nowrap;">${q.score_weight} Poin</span>

                </td>

                <td>

                    <code style="display: block; max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #38bdf8;" title="${escapeHtml(q.expected_query || '-')}">

                        ${escapeHtml(q.expected_query || "-")}

                    </code>

                </td>

                <td style="width: 140px; text-align: right; white-space: nowrap;">

                    <button type="button" class="btn btn-secondary btn-sm" onclick="openEditQuestionModal(${q.id})">Edit</button>

                    <button type="button" class="btn btn-danger btn-sm" onclick="deleteQuestion(${q.id})">Hapus</button>

                </td>

            </tr>

        `;

    });

    tbody.innerHTML = html;

}



function updateNextQuestionNumber() {
    const idInput = document.getElementById("modal-q-id");
    const category = document.getElementById("modal-q-category");
    const number = document.getElementById("modal-q-num");
    if (!category || !number) return;
    const target = (category.value || "A").toUpperCase();
    if (idInput?.value && target === idInput.dataset.originalCategory) {
        number.value = idInput.dataset.originalNumber;
        return;
    }
    const numbers = questionsList.filter(q => (q.category || "A").toUpperCase() === target && String(q.id) !== idInput?.value)
        .map(q => Number(q.question_number) || 0);
    number.value = Math.max(0, ...numbers) + 1;
}


let currentQuestionGradingPolicy = {};

function updateQuestionKeyRequirement() {
    const key = document.getElementById("modal-q-expected");
    if (key) key.required = document.getElementById("modal-q-grading-mode")?.value !== "manual";
}

function setQuestionGradingPolicy(policy) {
    currentQuestionGradingPolicy = policy;
    const mode = document.getElementById("modal-q-grading-mode");
    if (mode) mode.value = policy.mode === "manual" ? "manual" : "automatic";
    updateQuestionKeyRequirement();
}

function openAddQuestionModal() {

    document.getElementById("q-modal-title").textContent = "Tambah Soal Ujian Baru";

    document.getElementById("modal-q-id").value = "";

    const catSelect = document.getElementById("modal-q-category");
    if (catSelect && packagesList && packagesList.length > 0) {
        catSelect.innerHTML = packagesList.map(p => `
            <option value="${escapeHtml(p.code)}">Paket ${escapeHtml(p.code)} - ${escapeHtml(p.name)}</option>
        `).join("");
    }

    // Default to current category filter if set, else first package or "A"

    const filterVal = document.getElementById("filter-question-category")?.value || (packagesList && packagesList[0] ? packagesList[0].code : "A");

    if (catSelect && filterVal) {

        catSelect.value = filterVal;

    }



    updateNextQuestionNumber();



    document.getElementById("modal-q-title").value = "";

    document.getElementById("modal-q-desc").value = "";

    document.getElementById("modal-q-weight").value = "15";

    document.getElementById("modal-q-expected").value = "";
    setQuestionGradingPolicy({});

    const targetDbEl = document.getElementById("modal-q-target-db");

    if (targetDbEl) targetDbEl.value = "pagila";

    // Reset multi-image state
    currentQuestionDiagrams = [];
    currentQuestionOutputImages = [];
    renderQuestionDiagramsGallery();
    renderQuestionOutputGallery();

    const imgUrlEl = document.getElementById("modal-q-image-url");
    if (imgUrlEl) imgUrlEl.value = "";

    document.getElementById("question-modal").style.display = "flex";

}



function openEditQuestionModal(qId) {

    const q = questionsList.find(item => item.id === qId);

    if (!q) return;

    document.getElementById("q-modal-title").textContent = `Edit Soal #${q.question_number} (Paket ${q.category || "A"})`;

    document.getElementById("modal-q-id").value = q.id;
    document.getElementById("modal-q-id").dataset.editRevision = q.edit_revision;
    document.getElementById("modal-q-id").dataset.originalCategory = (q.category || "A").toUpperCase();
    document.getElementById("modal-q-id").dataset.originalNumber = q.question_number;

    const catSelect = document.getElementById("modal-q-category");
    if (catSelect && packagesList && packagesList.length > 0) {
        catSelect.innerHTML = packagesList.map(p => `
            <option value="${escapeHtml(p.code)}">Paket ${escapeHtml(p.code)} - ${escapeHtml(p.name)}</option>
        `).join("");
    }

    if (catSelect) {

        catSelect.value = q.category || "A";

    }

    document.getElementById("modal-q-num").value = q.question_number;

    document.getElementById("modal-q-title").value = q.title;

    document.getElementById("modal-q-desc").value = q.description;

    document.getElementById("modal-q-weight").value = q.score_weight;

    document.getElementById("modal-q-expected").value = q.expected_query || "";
    setQuestionGradingPolicy(typeof q.grading_policy === "string" ? JSON.parse(q.grading_policy) : q.grading_policy || {});

    const targetDbEl = document.getElementById("modal-q-target-db");

    if (targetDbEl) targetDbEl.value = q.target_database || "pagila";

    // Parse question diagrams list
    if (Array.isArray(q.question_images_list)) {
        currentQuestionDiagrams = [...q.question_images_list];
    } else if (typeof q.question_images === "string" && q.question_images.trim()) {
        try {
            const parsed = JSON.parse(q.question_images);
            currentQuestionDiagrams = Array.isArray(parsed) ? parsed : [q.question_images.trim()];
        } catch {
            currentQuestionDiagrams = [q.question_images.trim()];
        }
    } else {
        currentQuestionDiagrams = [];
    }

    // Parse expected output images list
    if (Array.isArray(q.expected_output_images)) {
        currentQuestionOutputImages = [...q.expected_output_images];
    } else if (typeof q.expected_output_image === "string" && q.expected_output_image.trim()) {
        try {
            const parsed = JSON.parse(q.expected_output_image);
            currentQuestionOutputImages = Array.isArray(parsed) ? parsed : [q.expected_output_image.trim()];
        } catch {
            currentQuestionOutputImages = [q.expected_output_image.trim()];
        }
    } else {
        currentQuestionOutputImages = [];
    }

    renderQuestionDiagramsGallery();
    renderQuestionOutputGallery();

    const imgUrlEl = document.getElementById("modal-q-image-url");
    if (imgUrlEl) imgUrlEl.value = currentQuestionOutputImages[0] || "";

    document.getElementById("question-modal").style.display = "flex";

}



function closeQuestionModal() {

    document.getElementById("question-modal").style.display = "none";

}



async function saveQuestionForm(e) {

    e.preventDefault();

    const qId = document.getElementById("modal-q-id").value;

    const payload = {

        question_id: qId ? parseInt(qId) : null,
        expected_edit_revision: qId ? Number(document.getElementById("modal-q-id").dataset.editRevision) : null,

        category: (document.getElementById("modal-q-category")?.value || "A").trim().toUpperCase(),

        question_number: parseInt(document.getElementById("modal-q-num").value),

        title: document.getElementById("modal-q-title").value.trim(),

        description: document.getElementById("modal-q-desc").value.trim(),

        score_weight: Number(document.getElementById("modal-q-weight").value),

        expected_query: document.getElementById("modal-q-expected").value.trim(),

        grading_policy: {
            ...currentQuestionGradingPolicy,
            mode: document.getElementById("modal-q-grading-mode")?.value === "manual" ? "manual" : "automatic"
        },

        target_database: document.getElementById("modal-q-target-db")?.value || "pagila",

        question_images: currentQuestionDiagrams,

        expected_output_images: currentQuestionOutputImages,

        expected_output_image: currentQuestionOutputImages[0] || ""

    };



    if (!payload.title || !payload.description) {

        alert("Judul dan Deskripsi Soal wajib diisi!");

        return;

    }

    if (!Number.isInteger(payload.score_weight) || payload.score_weight < 1) {
        showToast("Bobot soal harus bilangan bulat positif.", "warning");
        return;
    }
    if (payload.grading_policy.mode === "automatic" && !payload.expected_query) {
        showToast("Kunci jawaban SQL wajib diisi untuk penilaian otomatis.", "warning");
        return;
    }



    try {

        const res = await fetch("/api/admin/questions", {

            method: "POST",

            headers: { "Content-Type": "application/json" },

            body: JSON.stringify(payload)

        });

        const data = await res.json();

        if (data.status === "success") {

            showToast("Soal berhasil disimpan.");

            closeQuestionModal();

            loadQuestionsData();

        } else {

            showToast(data.detail || data.message || "Gagal menyimpan soal.", "warning");

        }

    } catch (err) {

        showToast(err.message || "Soal belum dapat disimpan. Coba kembali.", err.status && err.status < 500 ? "warning" : "danger");

    }

}



async function deleteQuestion(qId) {

    if (!confirm("Hapus soal ini? Mahasiswa yang telah menjawab soal ini akan kehilangan submisi untuk nomor ini.")) return;

    try {

        const res = await fetch(`/api/admin/questions/${qId}?expected_edit_revision=${questionsList.find(q => q.id === qId)?.edit_revision || 0}`, { method: "DELETE" });

        const data = await res.json();

        showToast(data.detail || data.message || (res.ok ? "Soal berhasil dihapus." : "Gagal menghapus soal."), res.ok ? "success" : "warning");

        loadQuestionsData();

    } catch (err) {

        showToast(err.status ? err.message : "Gagal menghapus soal.", "danger");

    }

}



function openDeleteAllQuestionsModal() {

    const input = document.getElementById("delete-all-confirm-input");

    const confirmBtn = document.getElementById("btn-confirm-delete-all-questions");

    if (input) input.value = "";

    if (confirmBtn) confirmBtn.disabled = true;

    document.getElementById("delete-all-questions-modal").style.display = "flex";

}



async function deleteAllQuestions() {

    try {

        const res = await fetch("/api/admin/questions", { method: "DELETE" });

        const data = await res.json();

        document.getElementById("delete-all-questions-modal").style.display = "none";

        showToast(data.message || "Seluruh soal berhasil dihapus.", "success");

        loadQuestionsData();

    } catch (err) {

        showToast(err.status ? err.message : "Gagal menghapus semua soal.", "danger");

    }

}



// Window exposures for inline event bindings

window.openAddPackageModal = openAddPackageModal;

window.openEditPackageModal = openEditPackageModal;

window.closePackageModal = closePackageModal;

window.savePackageForm = savePackageForm;

window.confirmDeletePackage = confirmDeletePackage;

window.filterByPackage = filterByPackage;

window.openAddQuestionModal = openAddQuestionModal;

window.openEditQuestionModal = openEditQuestionModal;

window.closeQuestionModal = closeQuestionModal;

window.saveQuestionForm = saveQuestionForm;

window.deleteQuestion = deleteQuestion;

window.openDeleteAllQuestionsModal = openDeleteAllQuestionsModal;

window.deleteAllQuestions = deleteAllQuestions;

window.resetStudentCategory = resetStudentCategory;

window.kickStudent = kickStudent;

window.clearStudentSessionLock = clearStudentSessionLock;



// ==============================================================================
// 4. Database Sandbox Management & Table Data Browser
// ==============================================================================

let currentViewingTable = "";
let currentTableDataPage = 1;
let schemaViewRequest = 0;
let tableViewRequest = 0;

async function loadSchemaPreview(targetDb) {
    const container = document.getElementById("db-schema-preview");
    if (!container) return;
    const request = ++schemaViewRequest;

    const dbSelector = document.getElementById("admin-db-selector");
    const currentDb = (targetDb || (dbSelector ? dbSelector.value : "pagila") || "pagila").toLowerCase();

    if (dbSelector && dbSelector.value !== currentDb) {
        dbSelector.value = currentDb;
    }

    const titleEl = document.getElementById("admin-schema-preview-title");
    const viewerDbName = document.getElementById("admin-data-viewer-db-name");
    if (viewerDbName) {
        viewerDbName.textContent = currentDb;
    }

    if (titleEl) {
        titleEl.textContent = `Preview Skema Tabel '${currentDb}':`;
    }

    container.innerHTML = `<p style="color: var(--text-muted);"><span class="spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:6px;"></span>Memuat struktur database '${escapeHtml(currentDb)}'...</p>`;

    try {
        const res = await fetch(`/api/admin/schema?db=${encodeURIComponent(currentDb)}`);
        const data = await res.json();
        if (request !== schemaViewRequest) return;
        const schema = data.schema || {};
        const tables = Object.keys(schema);

        if (titleEl) {
            titleEl.textContent = `Preview Skema Tabel '${currentDb}' (${tables.length} Tabel):`;
        }

        // Populate table select dropdown in Data Viewer
        const tableSelect = document.getElementById("admin-table-data-select");
        if (tableSelect) {
            const prevSelected = tableSelect.value;
            tableSelect.innerHTML = `<option value="">-- Pilih Tabel (${tables.length} Tabel) --</option>`;
            tables.forEach(tbl => {
                const opt = document.createElement("option");
                opt.value = tbl;
                opt.textContent = `${tbl} (${schema[tbl].length} kolom)`;
                tableSelect.appendChild(opt);
            });
            if (tables.includes(prevSelected)) {
                tableSelect.value = prevSelected;
            } else if (tables.length > 0) {
                const preferred = ["film", "customer", "actor", "customers", "orders"].find(t => tables.includes(t)) || tables[0];
                tableSelect.value = preferred;
                loadTableData(preferred, 1);
            }
        }

        if (tables.length === 0) {
            container.innerHTML = `<p style="color: var(--text-muted); padding: 1rem;">Belum ada tabel di database '${escapeHtml(currentDb)}'.</p>`;
            return;
        }

        let html = "";
        tables.forEach(table => {
            const cols = schema[table];
            html += `
                <div style="background: #0f172a; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.75rem; margin-bottom: 0.75rem;">
                    <div style="font-weight: 600; color: #93c5fd; margin-bottom: 0.4rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.4rem;">
                        <span style="font-family: var(--font-mono);">📁 Tabel: <strong>${escapeHtml(table)}</strong> (${cols.length} Kolom)</span>
                        <button type="button" class="btn btn-secondary btn-sm header-action btn-view-table-data" data-table="${escapeHtml(table)}" style="font-size: 0.72rem; padding: 0.15rem 0.55rem; color: #38bdf8; border-color: rgba(56, 189, 248, 0.4);">
                            👁️ Lihat Isi Data
                        </button>
                    </div>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.35rem;">
                        ${cols.map(c => `
                            <span style="font-size: 0.75rem; background: var(--bg-surface-elevated); padding: 0.15rem 0.45rem; border-radius: 3px; font-family: var(--font-mono); color: var(--text-main);">
                                ${c.pk ? '[PK] ' : ''}${escapeHtml(c.name)} (${escapeHtml(c.type)})
                            </span>
                        `).join("")}
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;

        // Wire click listeners for "Lihat Isi Data" buttons
        container.querySelectorAll(".btn-view-table-data").forEach(btn => {
            btn.addEventListener("click", () => {
                const tbl = btn.getAttribute("data-table");
                if (tableSelect) tableSelect.value = tbl;
                loadTableData(tbl, 1);
                document.getElementById("admin-table-data-container")?.scrollIntoView({ behavior: "smooth", block: "center" });
            });
        });

    } catch (err) {
        if (request !== schemaViewRequest) return;
        container.innerHTML = `<p style="color: #ef4444;">Gagal memuat preview skema database '${escapeHtml(currentDb)}'.</p>`;
    }
}

async function loadTableData(tableName, page = 1) {
    if (!tableName) return;
    const request = ++tableViewRequest;
    currentViewingTable = tableName;
    currentTableDataPage = page;

    const dbSelector = document.getElementById("admin-db-selector");
    const currentDb = ((dbSelector ? dbSelector.value : "pagila") || "pagila").toLowerCase();
    const isCurrentView = () => request === tableViewRequest &&
        ((dbSelector?.value || "pagila").toLowerCase() === currentDb);

    const limitSelect = document.getElementById("admin-table-data-limit");
    const limit = parseInt(limitSelect ? limitSelect.value : "25", 10) || 25;
    const offset = (page - 1) * limit;

    const container = document.getElementById("admin-table-data-container");
    const paginationBar = document.getElementById("admin-table-data-pagination");
    const infoEl = document.getElementById("admin-table-data-info");
    const prevBtn = document.getElementById("btn-table-data-prev");
    const nextBtn = document.getElementById("btn-table-data-next");

    if (!container) return;

    container.innerHTML = `<p style="color: var(--text-muted); padding: 1.5rem; text-align: center;"><span class="spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:6px;"></span>Memuat data tabel '${escapeHtml(tableName)}'...</p>`;

    try {
        const res = await fetch(`/api/admin/table-data?db=${encodeURIComponent(currentDb)}&table=${encodeURIComponent(tableName)}&limit=${limit}&offset=${offset}`);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "Gagal mengambil data tabel");
        }
        const data = await res.json();
        if (!isCurrentView()) return;
        const columns = data.columns || [];
        const rows = data.rows || [];
        const totalRows = typeof data.total_rows === "number" ? data.total_rows : rows.length;
        const totalPages = Math.max(1, Math.ceil(totalRows / limit));

        if (rows.length === 0) {
            container.innerHTML = `<p style="color: var(--text-muted); padding: 1.5rem; text-align: center;">Tabel '${escapeHtml(tableName)}' kosong (0 baris data).</p>`;
            if (paginationBar) paginationBar.style.display = "none";
            return;
        }

        // Render HTML Table with sticky header
        let html = `
            <table class="data-table" style="width: 100%; border-collapse: collapse; font-size: 0.8rem;">
                <thead style="position: sticky; top: 0; background: var(--bg-surface-elevated); z-index: 2;">
                    <tr>
                        <th style="padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border-subtle); color: #94a3b8; text-align: center; width: 40px;">#</th>
                        ${columns.map(c => `<th style="padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border-subtle); color: #38bdf8; text-align: left; font-family: var(--font-mono);">${escapeHtml(c)}</th>`).join("")}
                    </tr>
                </thead>
                <tbody>
        `;

        rows.forEach((row, idx) => {
            const rowNum = offset + idx + 1;
            html += `<tr style="border-bottom: 1px solid rgba(255,255,255,0.04); background: ${idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)'};">
                <td style="padding: 0.35rem 0.6rem; color: var(--text-muted); text-align: center; font-size: 0.75rem;">${rowNum}</td>
                ${row.map(cell => {
                    const isNull = cell === null;
                    const cellText = isNull ? '<span style="color:#64748b; font-style:italic;">NULL</span>' : escapeHtml(String(cell));
                    return `<td style="padding: 0.35rem 0.6rem; white-space: nowrap; max-width: 250px; overflow: hidden; text-overflow: ellipsis;">${cellText}</td>`;
                }).join("")}
            </tr>`;
        });

        html += `</tbody></table>`;
        container.innerHTML = html;

        // Update pagination bar
        if (paginationBar) {
            paginationBar.style.display = "flex";
            if (infoEl) {
                const endRow = Math.min(offset + rows.length, totalRows);
                infoEl.textContent = `Menampilkan baris ${offset + 1} - ${endRow} dari total ${totalRows} baris di '${tableName}' (Halaman ${page}/${totalPages})`;
            }
            if (prevBtn) prevBtn.disabled = (page <= 1);
            if (nextBtn) nextBtn.disabled = (page >= totalPages);
        }

    } catch (err) {
        if (!isCurrentView()) return;
        container.innerHTML = `<p style="color: #ef4444; padding: 1.5rem; text-align: center;">Error: ${escapeHtml(err.message)}</p>`;
        if (paginationBar) paginationBar.style.display = "none";
    }
}



async function confirmResetDatabase() {

    const dbSelector = document.getElementById("admin-db-selector");

    const currentDb = (dbSelector ? dbSelector.value : "classicmodels") || "classicmodels";

    if (!confirm(`Peringatan: Tindakan ini akan mengosongkan dan mengembalikan database '${currentDb}' ke kondisi awal dataset master. Lanjutkan?`)) {

        return;

    }



    const btn = document.getElementById("btn-reset-db");

    btn.disabled = true;

    btn.textContent = `Mereset Database ${currentDb}...`;



    try {

        const res = await fetch("/api/admin/reset-database", {

            method: "POST",

            headers: { "Content-Type": "application/json" },

            body: JSON.stringify({ target_db: currentDb })

        });

        const data = await res.json();

        if (res.ok && data.status === "success") {

            showToast(`Database '${currentDb}' berhasil di-reset!`);

            loadSchemaPreview(currentDb);

        } else {

            showToast(data.detail || data.message || "Gagal mereset database.", "danger");

        }

    } catch (err) {

        showToast(err.status ? err.message : "Error saat menghubungi server untuk reset database.", "danger");

    } finally {

        btn.disabled = false;

        btn.textContent = "Reset Database";

    }

}



async function handleSqlUpload(e) {

    e.preventDefault();

    const fileInput = document.getElementById("sql-file-input");

    if (!fileInput.files || fileInput.files.length === 0) {

        alert("Pilih file .sql terlebih dahulu!");

        return;

    }



    const targetDbSelect = document.getElementById("sql-upload-target-db");

    const targetDb = (targetDbSelect ? targetDbSelect.value : "classicmodels") || "classicmodels";



    const formData = new FormData();

    formData.append("file", fileInput.files[0]);

    formData.append("target_db", targetDb);



    const uploadBtn = document.getElementById("btn-upload-sql");

    uploadBtn.disabled = true;

    uploadBtn.textContent = `Mengeksekusi SQL ke ${targetDb}...`;



    try {

        const res = await fetch("/api/admin/upload-sql", {

            method: "POST",

            body: formData

        });

        const data = await res.json();

        if (res.ok && data.status === "success") {

            showToast(`File SQL berhasil dieksekusi ke database '${targetDb}'!`);

            fileInput.value = "";

            const dbSelector = document.getElementById("admin-db-selector");

            if (dbSelector) dbSelector.value = targetDb;

            loadSchemaPreview(targetDb);

        } else {

            showToast(data.detail || data.message || "Eksekusi SQL gagal.", "danger");

        }

    } catch (err) {

        showToast(err.status ? err.message : "Error saat mengunggah file SQL.", "danger");

    } finally {

        uploadBtn.disabled = false;

        uploadBtn.textContent = "Unggah & Eksekusi";

    }

}



// ==============================================================================

// 5. Exam Event Logs

// ==============================================================================

let logsCurrentPage = 1;
const logsPerPage = 50;

async function loadLogsData(page = 1) {
    logsCurrentPage = page;
    const offset = (page - 1) * logsPerPage;
    const tbody = document.getElementById("logs-table-body");
    const infoEl = document.getElementById("logs-pagination-info");
    const prevBtn = document.getElementById("btn-logs-prev");
    const nextBtn = document.getElementById("btn-logs-next");
    if (!tbody) return;

    try {
        const res = await fetch(`/api/admin/logs?limit=${logsPerPage}&offset=${offset}`);
        const data = await res.json();
        const logs = data.logs || [];
        const total = typeof data.total === "number" ? data.total : logs.length;
        const totalPages = Math.max(1, Math.ceil(total / logsPerPage));

        if (infoEl) {
            infoEl.textContent = `Halaman ${page} dari ${totalPages} (Total ${total} Log)`;
        }
        if (prevBtn) prevBtn.disabled = (page <= 1);
        if (nextBtn) nextBtn.disabled = (page >= totalPages);

        if (logs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">Belum ada log aktivitas.</td></tr>`;
            return;
        }

        let html = "";
        logs.forEach(l => {
            let badgeClass = "badge-offline";
            if (l.action_type === "login") badgeClass = "badge-online";
            if (l.action_type === "submit") badgeClass = "badge-online";
            if (l.action_type === "blur_tab" || l.action_type === "paste_attempt") badgeClass = "badge-danger";
            if (l.action_type === "ip_switch_kick") badgeClass = "badge-warning";

            html += `
                <tr>
                    <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-muted);">${escapeHtml(l.timestamp || "-")}</td>
                    <td><strong>${escapeHtml(l.nim || "Sistem")}</strong></td>
                    <td>${escapeHtml(l.name || "-")}</td>
                    <td><span class="status-badge ${badgeClass}">${escapeHtml(l.action_type)}</span></td>
                    <td style="font-size: 0.85rem; color: var(--text-main);">${escapeHtml(l.details || "-")}</td>
                </tr>
            `;
        });
        tbody.innerHTML = html;
    } catch (err) {
        console.error("Failed loading logs:", err);
    }
}



// ==============================================================================

// 6. Toast Notification Helper

// ==============================================================================

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

    if (!str) return "";

    return String(str)

        .replace(/&/g, "&amp;")

        .replace(/</g, "&lt;")

        .replace(/>/g, "&gt;")

        .replace(/"/g, "&quot;")

        .replace(/'/g, "&#039;");

}



// ==============================================================================

// 7. Ubah Password Master Admin

// ==============================================================================

function openChangePasswordModal() {

    const alertBox = document.getElementById("change-pw-alert");

    if (alertBox) {

        alertBox.style.display = "none";

        alertBox.textContent = "";

    }

    const currentInput = document.getElementById("current-master-pw");

    const newInput = document.getElementById("new-master-pw");

    const confirmInput = document.getElementById("confirm-master-pw");

    if (currentInput) currentInput.value = "";

    if (newInput) newInput.value = "";

    if (confirmInput) confirmInput.value = "";



    const modal = document.getElementById("change-pw-modal");

    if (modal) {

        modal.style.display = "flex";

        currentInput?.focus();

    }

}



async function handleChangePassword(e) {

    e.preventDefault();

    const currentPw = document.getElementById("current-master-pw").value;

    const newPw = document.getElementById("new-master-pw").value;

    const confirmPw = document.getElementById("confirm-master-pw").value;

    const alertBox = document.getElementById("change-pw-alert");



    const showAlert = (msg, isSuccess = false) => {

        if (!alertBox) return;

        alertBox.style.display = "block";

        if (isSuccess) {

            alertBox.style.backgroundColor = "rgba(16, 185, 129, 0.15)";

            alertBox.style.color = "#34d399";

            alertBox.style.border = "1px solid rgba(16, 185, 129, 0.3)";

        } else {

            alertBox.style.backgroundColor = "rgba(239, 68, 68, 0.15)";

            alertBox.style.color = "#f87171";

            alertBox.style.border = "1px solid rgba(239, 68, 68, 0.3)";

        }

        alertBox.textContent = msg;

    };



    if (newPw !== confirmPw) {

        showAlert("Konfirmasi password baru tidak cocok!");

        return;

    }



    if (newPw.length < 12) {

        showAlert("Password baru minimal 12 karakter!");

        return;

    }



    const btn = document.getElementById("btn-save-master-pw");

    btn.disabled = true;

    btn.textContent = "Menyimpan...";



    try {

        const res = await fetch("/api/admin/change-password", {

            method: "POST",

            headers: { "Content-Type": "application/json" },

            body: JSON.stringify({ current_password: currentPw, new_password: newPw })

        });

        const data = await res.json();

        if (res.ok && data.success) {

            showAlert("Password master admin berhasil diperbarui!", true);

            showToast("Password master admin berhasil diperbarui!", "success");
            window.location.reload();

            setTimeout(() => {

                document.getElementById("change-pw-modal").style.display = "none";

            }, 1200);

        } else {

            showAlert(data.detail || data.message || "Gagal mengubah password!");

        }

    } catch (err) {

        showAlert(err.status ? err.message : "Terjadi kesalahan jaringan.");

    } finally {

        btn.disabled = false;

        btn.textContent = "Simpan Password";

    }

}








// ==============================================================================

// Admin Question Multi-Image Upload & Gallery Handlers

// ==============================================================================

let currentQuestionDiagrams = [];
let currentQuestionOutputImages = [];

function renderQuestionDiagramsGallery() {
    const container = document.getElementById("modal-q-diagrams-gallery");
    if (!container) return;
    if (!currentQuestionDiagrams || currentQuestionDiagrams.length === 0) {
        container.innerHTML = "";
        container.style.display = "none";
        return;
    }
    container.style.display = "grid";
    container.innerHTML = currentQuestionDiagrams.map((url, idx) => `
        <div class="gallery-item-card" title="Klik untuk membuka ukuran penuh">
            <span class="gallery-item-badge">#${idx + 1}</span>
            <img src="${escapeHtml(url)}" alt="Diagram Soal ${idx + 1}" class="gallery-item-thumb" data-admin-action="open-image" data-url="${escapeHtml(url)}">
            <button type="button" class="gallery-item-remove" onclick="removeQuestionDiagram(${idx})" title="Hapus gambar ini">✕</button>
        </div>
    `).join("");
}

function removeQuestionDiagram(idx) {
    if (idx >= 0 && idx < currentQuestionDiagrams.length) {
        currentQuestionDiagrams.splice(idx, 1);
        renderQuestionDiagramsGallery();
    }
}

function renderQuestionOutputGallery() {
    const container = document.getElementById("modal-q-output-gallery");
    const legacyUrlInput = document.getElementById("modal-q-image-url");
    if (!container) return;
    if (legacyUrlInput) {
        legacyUrlInput.value = currentQuestionOutputImages[0] || "";
    }
    if (!currentQuestionOutputImages || currentQuestionOutputImages.length === 0) {
        container.innerHTML = "";
        container.style.display = "none";
        return;
    }
    container.style.display = "grid";
    container.innerHTML = currentQuestionOutputImages.map((url, idx) => `
        <div class="gallery-item-card" title="Klik untuk membuka ukuran penuh">
            <span class="gallery-item-badge">#${idx + 1}</span>
            <img src="${escapeHtml(url)}" alt="Output Tabel ${idx + 1}" class="gallery-item-thumb" data-admin-action="open-image" data-url="${escapeHtml(url)}">
            <button type="button" class="gallery-item-remove" onclick="removeQuestionOutputImage(${idx})" title="Hapus screenshot ini">✕</button>
        </div>
    `).join("");
}

function removeQuestionOutputImage(idx) {
    if (idx >= 0 && idx < currentQuestionOutputImages.length) {
        currentQuestionOutputImages.splice(idx, 1);
        renderQuestionOutputGallery();
    }
}

async function handleQuestionDiagramsUpload(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append("images", files[i]);
    }
    formData.append("image_type", "question");

    try {
        showToast(`Mengunggah ${files.length} gambar soal...`);
        const res = await fetch("/api/admin/questions/upload-images", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (data.status === "success" && Array.isArray(data.image_urls)) {
            data.image_urls.forEach(url => {
                if (!currentQuestionDiagrams.includes(url)) {
                    currentQuestionDiagrams.push(url);
                }
            });
            renderQuestionDiagramsGallery();
            showToast(`${data.count || data.image_urls.length} gambar soal berhasil ditambahkan!`, "success");
        } else {
            showToast(data.detail || "Gagal mengunggah gambar soal.", "danger");
        }
    } catch (err) {
        showToast(err.message || "Error saat mengunggah file gambar soal.", "danger");
    } finally {
        e.target.value = "";
    }
}

async function handleQuestionOutputImagesUpload(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append("images", files[i]);
    }
    formData.append("image_type", "output");

    try {
        showToast(`Mengunggah ${files.length} screenshot output...`);
        const res = await fetch("/api/admin/questions/upload-images", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (data.status === "success" && Array.isArray(data.image_urls)) {
            data.image_urls.forEach(url => {
                if (!currentQuestionOutputImages.includes(url)) {
                    currentQuestionOutputImages.push(url);
                }
            });
            renderQuestionOutputGallery();
            showToast(`${data.count || data.image_urls.length} screenshot output berhasil ditambahkan!`, "success");
        } else {
            showToast(data.detail || "Gagal mengunggah screenshot output.", "danger");
        }
    } catch (err) {
        showToast(err.message || "Error saat mengunggah screenshot output.", "danger");
    } finally {
        e.target.value = "";
    }
}

async function handleQuestionImageUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("image", file);
    formData.append("image_type", "output");

    try {
        showToast("Mengunggah screenshot tabel...");
        const res = await fetch("/api/admin/questions/upload-image", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (data.status === "success" && data.image_url) {
            if (!currentQuestionOutputImages.includes(data.image_url)) {
                currentQuestionOutputImages.push(data.image_url);
            }
            renderQuestionOutputGallery();
            showToast("Screenshot berhasil diunggah!", "success");
        } else {
            showToast(data.detail || "Gagal mengunggah gambar.", "danger");
        }
    } catch (err) {
        showToast("Error saat mengunggah file gambar.", "danger");
    } finally {
        e.target.value = "";
    }
}

function clearQuestionImage() {
    currentQuestionOutputImages = [];
    renderQuestionOutputGallery();
}

window.handleQuestionDiagramsUpload = handleQuestionDiagramsUpload;
window.handleQuestionOutputImagesUpload = handleQuestionOutputImagesUpload;
window.removeQuestionDiagram = removeQuestionDiagram;
window.removeQuestionOutputImage = removeQuestionOutputImage;
window.handleQuestionImageUpload = handleQuestionImageUpload;
window.clearQuestionImage = clearQuestionImage;


document.addEventListener('click',event=>{
    const button=event.target.closest('[data-admin-action]');
    if (!button) return;
    const d=button.dataset;
    const actions={
        unlock:()=>clearStudentSessionLock(d.nim,d.name),
        'reset-package':()=>resetStudentCategory(d.nim,d.name),
        'edit-package':()=>openEditPackageModal(d.code),
        'delete-package':()=>confirmDeletePackage(d.code,Number(d.questions),Number(d.students)),
        'filter-package':()=>filterByPackage(d.code),
        'open-image':()=>{
            const url=new URL(d.url,window.location.href);
            if(['http:','https:'].includes(url.protocol)) window.open(url.href,'_blank','noopener,noreferrer');
        }
    };
    if(actions[d.adminAction]) actions[d.adminAction]();
});
