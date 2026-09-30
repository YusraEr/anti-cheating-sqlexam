import os
import uuid
import socket
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI, 
    Request, 
    Response, 
    Depends, 
    HTTPException, 
    status, 
    Form, 
    UploadFile, 
    File
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Initialize SQLite tables on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_system_db()
    health = database.verify_system_health()
    print("---------------------------------------------------------")
    print("Offline SQL Exam System Server Starting...")
    print(f"System SQLite DB Status      : {'OK' if health['sqlite'] else 'ERROR'}")
    print(f"PostgreSQL Admin Status      : {'OK' if health['postgres_admin'] else 'ERROR'}")
    print(f"PostgreSQL student_role Status: {'OK' if health['postgres_student'] else 'ERROR'}")
    print(f"Host Local IP Address        : http://{get_local_host_ip()}:8000")
    print("---------------------------------------------------------")
    yield

app = FastAPI(title="Offline SQL Exam System", lifespan=lifespan)

# Mount Static Files and Templates
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Master Key for Admin / Lecturer
ADMIN_SESSION_TOKEN = "exam_admin_auth_token_secret_key"

def get_local_host_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_client_ip(request: Request) -> str:
    # In offline Wi-Fi router setup, client.host is the exact device IP
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"

# ==============================================================================
# Pydantic Request Models
# ==============================================================================
class RunQueryRequest(BaseModel):
    nim: str
    session_token: str
    query: str

class SubmitAnswerRequest(BaseModel):
    nim: str
    session_token: str
    question_id: int
    query: str

class HeartbeatRequest(BaseModel):
    nim: str
    session_token: str

class LogEventRequest(BaseModel):
    nim: str
    session_token: str
    action_type: str
    details: Optional[str] = None

class SaveQuestionRequest(BaseModel):
    question_id: Optional[int] = None
    question_number: int
    title: str
    description: str
    score_weight: int = 15
    expected_query: Optional[str] = None

class KickStudentRequest(BaseModel):
    nim: str

class ClearSessionsRequest(BaseModel):
    clear_logs: bool = False

# ==============================================================================
# Student Web Navigation Routes
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
async def index_route(request: Request):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    client_ip = get_client_ip(request)

    if token and nim:
        is_valid, user, _ = database.validate_student_session(nim, token, client_ip)
        if is_valid:
            return RedirectResponse(url="/exam", status_code=status.HTTP_302_FOUND)

    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None, message: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "message": message}
    )

@app.post("/login")
async def handle_login(
    request: Request,
    nim: str = Form(...),
    name: str = Form(...)
):
    nim = nim.strip()
    name = name.strip()
    if not nim or not name:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "NIM dan Nama Lengkap wajib diisi!", "nim_value": nim, "name_value": name}
        )

    client_ip = get_client_ip(request)
    new_session_token = str(uuid.uuid4())

    success, user, error_msg = database.login_student(nim, name, client_ip, new_session_token)

    if not success:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": error_msg, "nim_value": nim, "name_value": name}
        )

    response = RedirectResponse(url="/exam", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="student_token", value=new_session_token, httponly=True)
    response.set_cookie(key="student_nim", value=nim, httponly=True)
    return response

@app.get("/exam", response_class=HTMLResponse)
async def exam_page(request: Request):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    client_ip = get_client_ip(request)

    if not token or not nim:
        return RedirectResponse(url="/login?error=Silakan+login+terlebih+dahulu", status_code=status.HTTP_302_FOUND)

    is_valid, user, reason = database.validate_student_session(nim, token, client_ip)
    if not is_valid:
        response = RedirectResponse(url=f"/login?error={reason}", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("student_token")
        response.delete_cookie("student_nim")
        return response

    return templates.TemplateResponse(
        request=request,
        name="student.html",
        context={"user": user}
    )

@app.get("/logout")
async def logout_route(request: Request):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    if token and nim:
        database.logout_student(nim, token)

    response = RedirectResponse(url="/login?message=Anda+telah+berhasil+logout", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("student_token")
    response.delete_cookie("student_nim")
    return response

# ==============================================================================
# Student API Endpoints (Sandboxed)
# ==============================================================================

@app.get("/api/student/questions")
async def api_student_questions(request: Request, token: Optional[str] = None):
    token = token or request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    client_ip = get_client_ip(request)

    if not token or not nim:
        raise HTTPException(status_code=401, detail="Sesi tidak ditemukan.")

    is_valid, user, reason = database.validate_student_session(nim, token, client_ip)
    if not is_valid or not user:
        raise HTTPException(status_code=401, detail=reason)

    questions = database.get_all_questions(include_expected_query=False)
    submissions = database.get_student_submissions(user["id"])
    return {
        "questions": questions,
        "submissions": submissions
    }

@app.post("/api/student/run-query")
async def api_student_run_query(req: RunQueryRequest, request: Request):
    client_ip = get_client_ip(request)
    is_valid, user, reason = database.validate_student_session(req.nim, req.session_token, client_ip)
    if not is_valid or not user:
        return {"success": False, "error": f"Otorisasi Sesi Gagal: {reason}"}

    # Execute inside PostgreSQL sandbox
    result = database.execute_student_query(req.query)
    
    # Log query execution
    database.log_exam_event(
        user["id"], 
        "run_query", 
        f"Eksekusi query ({len(req.query)} karakter) - Status: {'Sukses' if result.get('success') else 'Error'}"
    )
    return result

@app.post("/api/student/submit")
async def api_student_submit(req: SubmitAnswerRequest, request: Request):
    client_ip = get_client_ip(request)
    is_valid, user, reason = database.validate_student_session(req.nim, req.session_token, client_ip)
    if not is_valid or not user:
        return {"status": "error", "message": f"Otorisasi Sesi Gagal: {reason}"}

    result = database.save_submission(user["id"], req.question_id, req.query)
    return result

@app.post("/api/student/heartbeat")
async def api_student_heartbeat(req: HeartbeatRequest, request: Request):
    client_ip = get_client_ip(request)
    is_valid, user, reason = database.validate_student_session(req.nim, req.session_token, client_ip)
    if is_valid and user:
        database.update_student_heartbeat(user["id"])
        return {"valid": True}
    return {"valid": False, "reason": reason}

@app.post("/api/student/log-event")
async def api_student_log_event(req: LogEventRequest, request: Request):
    client_ip = get_client_ip(request)
    is_valid, user, _ = database.validate_student_session(req.nim, req.session_token, client_ip)
    user_id = user["id"] if (is_valid and user) else None

    database.log_exam_event(user_id, req.action_type, req.details or "")
    return {"status": "ok"}

@app.get("/api/student/schema")
async def api_student_schema():
    schema = database.get_classicmodels_schema()
    return {"schema": schema}

# ==============================================================================
# Admin & Lecturer Portal Routes
# ==============================================================================

def verify_admin_auth(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    return token == ADMIN_SESSION_TOKEN

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": error}
    )

@app.post("/admin/login")
async def admin_handle_login(master_key: str = Form(...)):
    conn = database.get_sqlite_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_settings WHERE key = 'admin_master_key';")
    row = cur.fetchone()
    conn.close()

    stored_key = row["value"] if row else "admin123"
    if master_key.strip() == stored_key:
        res = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        res.set_cookie(key="admin_session", value=ADMIN_SESSION_TOKEN, httponly=True)
        return res

    return RedirectResponse(url="/admin/login?error=Master+Key+salah!", status_code=status.HTTP_302_FOUND)

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not verify_admin_auth(request):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

    host_ip = get_local_host_ip()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"host_ip": host_ip}
    )

@app.get("/admin/logout")
async def admin_logout():
    res = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    res.delete_cookie("admin_session")
    return res

# ------------------------------------------------------------------------------
# Admin REST APIs
# ------------------------------------------------------------------------------

@app.get("/api/admin/students")
async def api_admin_students(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    students = database.get_all_students_progress()
    return {"students": students}

@app.post("/api/admin/kick-student")
async def api_admin_kick_student(req: KickStudentRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    
    conn = database.get_sqlite_conn()
    with conn:
        conn.execute(
            """
            UPDATE users 
            SET session_token = 'KICKED_BY_ADMIN', status = 'offline' 
            WHERE nim = ?;
            """,
            (req.nim,)
        )
        conn.execute(
            """
            INSERT INTO exam_logs (action_type, details)
            VALUES ('kick', ?);
            """,
            (f"Pengawas me-reset sesi untuk NIM {req.nim}",)
        )
    conn.close()
    return {"status": "success", "message": f"Sesi mahasiswa {req.nim} telah direset."}

@app.post("/api/admin/stop-all-sessions")
async def api_admin_stop_all_sessions(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    count = database.stop_all_active_sessions()
    return {"status": "success", "message": f"Semua sesi aktif telah dihentikan ({count} sesi dinonaktifkan)."}

@app.post("/api/admin/clear-logs")
async def api_admin_clear_logs(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    count = database.clear_exam_logs()
    return {"status": "success", "message": f"Log aktivitas berhasil dibersihkan ({count} entri dihapus)."}

@app.post("/api/admin/clear-sessions")
async def api_admin_clear_sessions(req: ClearSessionsRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    result = database.clear_previous_sessions(clear_logs=req.clear_logs)
    return {
        "status": "success",
        "message": f"Data sesi sebelumnya berhasil dibersihkan. {result['users_cleared']} mahasiswa dan {result['submissions_cleared']} submisi dihapus."
    }

@app.get("/api/admin/questions")
async def api_admin_questions(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    questions = database.get_all_questions(include_expected_query=True)
    return {"questions": questions}

@app.post("/api/admin/questions")
async def api_admin_save_question(req: SaveQuestionRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    database.save_question(
        question_number=req.question_number,
        title=req.title,
        description=req.description,
        score_weight=req.score_weight,
        expected_query=req.expected_query,
        question_id=req.question_id
    )
    return {"status": "success", "message": "Soal ujian berhasil disimpan."}

@app.delete("/api/admin/questions/{question_id}")
async def api_admin_delete_question(question_id: int, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    database.delete_question(question_id)
    return {"status": "success", "message": "Soal ujian berhasil dihapus."}

@app.get("/api/admin/schema")
async def api_admin_schema(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    schema = database.get_classicmodels_schema()
    return {"schema": schema}

@app.post("/api/admin/reset-database")
async def api_admin_reset_database(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    success, msg = database.reset_classicmodels_database()
    return {"status": "success" if success else "error", "message": msg}

@app.post("/api/admin/upload-sql")
async def api_admin_upload_sql(request: Request, file: UploadFile = File(...)):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    
    content_bytes = await file.read()
    try:
        sql_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        sql_text = content_bytes.decode("latin-1")

    success, msg = database.reset_classicmodels_database(custom_sql_text=sql_text)
    return {"status": "success" if success else "error", "message": msg}

@app.get("/api/admin/logs")
async def api_admin_logs(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    logs = database.get_recent_exam_logs(limit=50)
    return {"logs": logs}

@app.get("/api/admin/export/csv")
async def api_admin_export_csv(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    csv_data = database.export_all_submissions_csv()
    response = Response(content=csv_data, media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=hasil_ujian_praktikum.csv"
    return response

@app.get("/api/admin/export/zip")
async def api_admin_export_zip(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    zip_bytes = database.export_submissions_zip_bytes()
    response = Response(content=zip_bytes, media_type="application/zip")
    response.headers["Content-Disposition"] = "attachment; filename=jawaban_sql_mahasiswa.zip"
    return response

# ==============================================================================
# Main Runner Entry Point
# ==============================================================================
if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so clients on local router Wi-Fi can connect
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
