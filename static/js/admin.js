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
let monitorInterval = null;

document.addEventListener("DOMContentLoaded", () => {
    initAdminTabs();
    loadDashboardData();
    loadQuestionsData();
    loadSchemaPreview();
    loadLogsData();

    // Start auto refresh for student monitoring every 6 seconds
    monitorInterval = setInterval(() => {
        if (activeTab === "monitoring") {
            loadDashboardData();
        }
    }, 6000);

    // Event Bindings
    document.getElementById("btn-refresh-monitor")?.addEventListener("click", loadDashboardData);
    document.getElementById("btn-stop-all-sessions")?.addEventListener("click", confirmStopAllSessions);
    document.getElementById("btn-clear-sessions")?.addEventListener("click", openClearSessionsModal);
    document.getElementById("btn-confirm-clear-sessions")?.addEventListener("click", executeClearSessions);
    document.getElementById("btn-add-question")?.addEventListener("click", openAddQuestionModal);
    document.getElementById("btn-reset-db")?.addEventListener("click", confirmResetDatabase);
    document.getElementById("btn-refresh-logs")?.addEventListener("click", loadLogsData);
    document.getElementById("btn-clear-logs")?.addEventListener("click", confirmClearLogs);
    document.getElementById("btn-export-csv")?.addEventListener("click", () => {
        window.location.href = "/api/admin/export/csv";
    });
    document.getElementById("btn-export-zip")?.addEventListener("click", () => {
        window.location.href = "/api/admin/export/zip";
    });

    // Custom SQL Upload Form
    document.getElementById("sql-upload-form")?.addEventListener("submit", handleSqlUpload);

    // Change Master Password Modal & Form
    document.getElementById("btn-open-change-pw-modal")?.addEventListener("click", openChangePasswordModal);
    document.getElementById("form-change-password")?.addEventListener("submit", handleChangePassword);
});

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
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">Belum ada mahasiswa yang login.</td></tr>`;
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

            html += `
                <tr>
                    <td><strong>${escapeHtml(s.nim)}</strong></td>
                    <td>${escapeHtml(s.name)}</td>
                    <td><code>${escapeHtml(s.ip_address || "-")}</code></td>
                    <td>${statusBadge}</td>
                    <td><strong style="color: #60a5fa;">${s.total_submitted}</strong> Soal</td>
                    <td>${cheatBadge}</td>
                    <td>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="kickStudent('${s.nim}')" title="Tendang sesi untuk memaksa login ulang">
                            Reset Sesi
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

async function kickStudent(nim) {
    if (!confirm(`Apakah Anda yakin ingin me-reset sesi mahasiswa dengan NIM: ${nim}?`)) return;
    try {
        const res = await fetch("/api/admin/kick-student", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nim: nim })
        });
        const data = await res.json();
        showToast(data.message || "Sesi berhasil direset.");
        loadDashboardData();
    } catch (err) {
        showToast("Gagal mereset sesi mahasiswa.", "danger");
    }
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
        showToast("Gagal menghentikan semua sesi.", "danger");
    } finally {
        btn.disabled = false;
        btn.textContent = "Hentikan Semua Sesi";
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
        showToast("Gagal membersihkan sesi.", "danger");
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
        showToast("Gagal membersihkan log.", "danger");
    } finally {
        btn.disabled = false;
        btn.textContent = "Bersihkan Semua Log";
    }
}

// ==============================================================================
// 3. Question Management (CRUD)
// ==============================================================================
async function loadQuestionsData() {
    try {
        const res = await fetch("/api/admin/questions");
        const data = await res.json();
        questionsList = data.questions || [];

        const tbody = document.getElementById("questions-table-body");
        if (!tbody) return;

        if (questionsList.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">Belum ada soal ujian yang dibuat.</td></tr>`;
            return;
        }

        let html = "";
        questionsList.forEach(q => {
            html += `
                <tr>
                    <td style="width: 70px;"><strong>#${q.question_number}</strong></td>
                    <td><strong>${escapeHtml(q.title)}</strong></td>
                    <td style="width: 100px;"><span class="weight-pill">${q.score_weight} Poin</span></td>
                    <td>
                        <code style="display: block; max-width: 450px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #38bdf8;">
                            ${escapeHtml(q.expected_query || "-")}
                        </code>
                    </td>
                    <td style="width: 160px; text-align: right;">
                        <button type="button" class="btn btn-secondary btn-sm" onclick="openEditQuestionModal(${q.id})">Edit</button>
                        <button type="button" class="btn btn-danger btn-sm" onclick="deleteQuestion(${q.id})">Hapus</button>
                    </td>
                </tr>
            `;
        });
        tbody.innerHTML = html;
    } catch (err) {
        console.error("Failed loading questions:", err);
    }
}

function openAddQuestionModal() {
    document.getElementById("q-modal-title").textContent = "Tambah Soal Ujian Baru";
    document.getElementById("modal-q-id").value = "";
    document.getElementById("modal-q-num").value = questionsList.length + 1;
    document.getElementById("modal-q-title").value = "";
    document.getElementById("modal-q-desc").value = "";
    document.getElementById("modal-q-weight").value = "15";
    document.getElementById("modal-q-expected").value = "";
    document.getElementById("question-modal").style.display = "flex";
}

function openEditQuestionModal(qId) {
    const q = questionsList.find(item => item.id === qId);
    if (!q) return;

    document.getElementById("q-modal-title").textContent = `Edit Soal #${q.question_number}`;
    document.getElementById("modal-q-id").value = q.id;
    document.getElementById("modal-q-num").value = q.question_number;
    document.getElementById("modal-q-title").value = q.title;
    document.getElementById("modal-q-desc").value = q.description;
    document.getElementById("modal-q-weight").value = q.score_weight;
    document.getElementById("modal-q-expected").value = q.expected_query || "";
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
        question_number: parseInt(document.getElementById("modal-q-num").value),
        title: document.getElementById("modal-q-title").value.trim(),
        description: document.getElementById("modal-q-desc").value.trim(),
        score_weight: parseInt(document.getElementById("modal-q-weight").value),
        expected_query: document.getElementById("modal-q-expected").value.trim()
    };

    if (!payload.title || !payload.description) {
        alert("Judul dan Deskripsi Soal wajib diisi!");
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
            showToast(data.message || "Gagal menyimpan soal.", "danger");
        }
    } catch (err) {
        showToast("Error koneksi server saat menyimpan soal.", "danger");
    }
}

async function deleteQuestion(qId) {
    if (!confirm("Hapus soal ini? Mahasiswa yang telah menjawab soal ini akan kehilangan submisi untuk nomor ini.")) return;
    try {
        const res = await fetch(`/api/admin/questions/${qId}`, { method: "DELETE" });
        const data = await res.json();
        showToast(data.message || "Soal berhasil dihapus.");
        loadQuestionsData();
    } catch (err) {
        showToast("Gagal menghapus soal.", "danger");
    }
}

// ==============================================================================
// 4. Database Sandbox Management
// ==============================================================================
async function loadSchemaPreview() {
    const container = document.getElementById("db-schema-preview");
    if (!container) return;

    try {
        const res = await fetch("/api/admin/schema");
        const data = await res.json();
        const schema = data.schema || {};

        let html = "";
        Object.keys(schema).forEach(table => {
            const cols = schema[table];
            html += `
                <div style="background: #0f172a; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 0.75rem; margin-bottom: 0.75rem;">
                    <div style="font-weight: 600; color: #93c5fd; margin-bottom: 0.4rem; display: flex; justify-content: space-between;">
                        <span>Tabel: ${table}</span>
                        <span style="font-size: 0.75rem; color: var(--text-muted);">${cols.length} Kolom</span>
                    </div>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.35rem;">
                        ${cols.map(c => `
                            <span style="font-size: 0.75rem; background: #1e293b; padding: 0.15rem 0.45rem; border-radius: 3px; font-family: var(--font-mono); color: ${c.pk ? '#f59e0b' : '#cbd5e1'};">
                                ${c.pk ? '[PK] ' : ''}${c.name} (${c.type})
                            </span>
                        `).join("")}
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p style="color: #ef4444;">Gagal memuat preview skema database.</p>`;
    }
}

async function confirmResetDatabase() {
    if (!confirm("Peringatan: Tindakan ini akan mengosongkan dan mengembalikan database 'classicmodels' ke kondisi awal dataset master. Lanjutkan?")) {
        return;
    }

    const btn = document.getElementById("btn-reset-db");
    btn.disabled = true;
    btn.textContent = "Mereset Database...";

    try {
        const res = await fetch("/api/admin/reset-database", { method: "POST" });
        const data = await res.json();
        if (data.status === "success") {
            showToast("Database classicmodels berhasil di-reset!");
            loadSchemaPreview();
        } else {
            showToast(data.message || "Gagal mereset database.", "danger");
        }
    } catch (err) {
        showToast("Error saat menghubungi server untuk reset database.", "danger");
    } finally {
        btn.disabled = false;
        btn.textContent = "Reset Database Sandbox";
    }
}

async function handleSqlUpload(e) {
    e.preventDefault();
    const fileInput = document.getElementById("sql-file-input");
    if (!fileInput.files || fileInput.files.length === 0) {
        alert("Pilih file .sql terlebih dahulu!");
        return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const uploadBtn = document.getElementById("btn-upload-sql");
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Mengeksekusi SQL...";

    try {
        const res = await fetch("/api/admin/upload-sql", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("File SQL berhasil dieksekusi ke PostgreSQL!");
            fileInput.value = "";
            loadSchemaPreview();
        } else {
            showToast(data.message || "Eksekusi SQL gagal.", "danger");
        }
    } catch (err) {
        showToast("Error saat mengunggah file SQL.", "danger");
    } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = "Upload & Eksekusi";
    }
}

// ==============================================================================
// 5. Exam Event Logs
// ==============================================================================
async function loadLogsData() {
    const tbody = document.getElementById("logs-table-body");
    if (!tbody) return;

    try {
        const res = await fetch("/api/admin/logs");
        const data = await res.json();
        const logs = data.logs || [];

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
                    <td style="font-size: 0.85rem; color: #cbd5e1;">${escapeHtml(l.details || "-")}</td>
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

    if (newPw.length < 4) {
        showAlert("Password baru minimal 4 karakter!");
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
        if (data.success) {
            showAlert("Password master admin berhasil diperbarui!", true);
            showToast("Password master admin berhasil diperbarui!", "success");
            setTimeout(() => {
                document.getElementById("change-pw-modal").style.display = "none";
            }, 1200);
        } else {
            showAlert(data.message || "Gagal mengubah password!");
        }
    } catch (err) {
        showAlert("Terjadi kesalahan jaringan.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Simpan Password";
    }
}

