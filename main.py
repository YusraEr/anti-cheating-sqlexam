import os
import uuid
import socket
import secrets
import hmac
from urllib.parse import urlsplit
import ipaddress
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Union
from contextlib import asynccontextmanager, closing

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
from pydantic import BaseModel, Field

import database
import admin_security
import admin_practice
import grading_queue
import asyncio

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Initialize SQLite tables on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_system_db()
    pg_ok, pg_msg = database.init_postgres_db()
    health = database.verify_system_health()
    if admin_security.check_key("admin123", database.get_admin_master_key()):
        print("[PERINGATAN KEAMANAN P0] Master key admin masih menggunakan password bawaan ('admin123').")
        print("  Disarankan mengganti password melalui portal admin (/admin) sebelum pelaksanaan ujian!")
    print("---------------------------------------------------------")
    print("Offline SQL Exam System Server Starting...")
    print(f"System SQLite DB Status      : {'OK' if health['sqlite'] else 'ERROR'}")
    print(f"PostgreSQL Sandbox Status    : {'OK' if pg_ok else 'PERHATIAN'}")
    print(f"Catatan PostgreSQL           : {pg_msg}")
    print(f"PostgreSQL Admin Status      : {'OK' if health['postgres_admin'] else 'ERROR'}")
    print(f"PostgreSQL student_role Status: {'OK' if health['postgres_student'] else 'ERROR'}")
    print(f"Host Local IP Address        : http://{get_local_host_ip()}:8000")
    print("---------------------------------------------------------")
    worker = grading_queue.Worker() if pg_ok else None
    practice_worker = admin_practice.Worker() if pg_ok else None
    app.state.grading_worker = worker
    app.state.practice_worker = practice_worker
    if worker:
        worker.start()
    if practice_worker:
        practice_worker.start()
    try:
        yield
    finally:
        if practice_worker:
            await asyncio.to_thread(practice_worker.stop)
        if worker:
            await asyncio.to_thread(worker.stop)
        database.close_pg_pools()

app = FastAPI(title="Offline SQL Exam System", lifespan=lifespan)

# Mount Static Files and Templates
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    try:
        verify_csrf_origin(request)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content={"detail":error.detail})
    exceeded=False
    if request.url.path in {'/api/admin/questions/upload-image','/api/admin/questions/upload-images','/api/admin/upload-sql'}:
        try:
            if not await asyncio.to_thread(verify_admin_auth, request):
                return JSONResponse(status_code=403, content={'detail':'Akses ditolak'})
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={'detail':error.detail})
        import question_uploads
        budget=(25*1024*1024 if request.url.path == '/api/admin/upload-sql' else question_uploads.MAX_BATCH_BYTES)+256*1024
        try:
            length=int(request.headers.get('content-length','0'))
        except ValueError:
            return JSONResponse(status_code=400,content={'detail':'Content-Length tidak valid.'})
        if length>budget or length<0:
            return JSONResponse(status_code=413,content={'detail':'Ukuran permintaan unggahan melebihi batas.'})
        receive=request._receive
        received=0
        async def bounded_receive():
            nonlocal received,exceeded
            message=await receive()
            received+=len(message.get('body',b''))
            if received>budget:
                exceeded=True
                raise HTTPException(status_code=413,detail='Ukuran permintaan unggahan melebihi batas.')
            return message
        request._receive=bounded_receive
    response = await call_next(request)
    if exceeded:
        response=JSONResponse(status_code=413,content={'detail':'Ukuran permintaan unggahan melebihi batas.'})
    if request.url.path.startswith('/static/uploads/questions/'):
        response.headers['Content-Security-Policy']="sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
        response.headers['X-Content-Type-Options']='nosniff'
    actor = getattr(request.state, "admin_session", None)
    if actor and request.method in {"POST","PUT","PATCH","DELETE"}:
        admin_security.audit(actor["id"], request.method, request.url.path, response.status_code, get_client_ip(request))
    # Mencegah Safari dan browser lain meng-cache file JS/CSS atau halaman dinamis selama sesi ujian
    if request.url.path.startswith(("/static/", "/api/student/", "/api/exam/", "/api/waiting/", "/api/admin/", "/admin")) or request.url.path in ["/", "/exam", "/waiting", "/admin", "/login"]:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response





import time
import threading
from collections import defaultdict

# ==============================================================================
# P0 Security Hardening: In-Memory Rate Limiter, CSRF, and Cookie Helpers
# ==============================================================================
class InMemoryRateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""
    def __init__(self):
        self._history = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if key not in self._history and len(self._history) >= 10000:
                for old_key in list(self._history):
                    if not self._history[old_key] or self._history[old_key][-1] <= now-60:
                        del self._history[old_key]
                if len(self._history) >= 10000:
                    return False
            timestamps = [t for t in self._history[key] if t > cutoff]
            if len(timestamps) >= max_requests:
                self._history[key] = timestamps
                return False
            timestamps.append(now)
            self._history[key] = timestamps
            return True

rate_limiter = InMemoryRateLimiter()

def verify_csrf_origin(request: Request) -> None:
    if request.method not in {"POST", "PUT", "DELETE", "PATCH"}:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Permintaan lintas situs ditolak.")
    expected = (request.url.scheme.lower(), request.url.netloc.lower())
    for name in ("origin", "referer"):
        value = request.headers.get(name)
        if value is None:
            continue
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme.lower(), parsed.netloc.lower()) == expected and not parsed.username and not parsed.password
            if name == "origin":
                valid = valid and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(status_code=403, detail="Permintaan ditolak: asal permintaan tidak valid.")


def set_secure_cookie(
    response: Response,
    key: str,
    value: str,
    request: Request,
    max_age: int = 28800  # 8 hours
) -> None:
    """Sets an audited secure, HttpOnly, SameSite=Lax cookie with explicit expiration."""
    is_https = request.url.scheme == "https"
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        samesite="lax",
        max_age=max_age,
        secure=is_https
    )

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

TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value.strip())
    for value in os.environ.get("TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()
)


def get_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "127.0.0.1"
    def trusted(address):
        try:
            return any(ipaddress.ip_address(address) in network for network in TRUSTED_PROXY_NETWORKS)
        except ValueError:
            return False
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and trusted(peer):
        try:
            chain = [str(ipaddress.ip_address(value.strip())) for value in forwarded.split(",")]
        except ValueError:
            return peer
        chain.append(peer)
        while len(chain) > 1 and trusted(chain[-1]):
            chain.pop()
        return chain[-1]
    return peer


@app.exception_handler(database.StudentAccessError)
async def student_access_error(request: Request, error: database.StudentAccessError):
    return JSONResponse(status_code=error.status_code, content={
        "detail": str(error), "code": error.code, "exam_state": error.exam_state,
    })


def require_student(request: Request, nim=None, token=None, *, active=False, category=False):
    practice = getattr(request.state, "practice_user", None)
    return database.require_student_access(
        nim if nim is not None else (practice["nim"] if practice else request.cookies.get("student_nim")),
        token if token is not None else (practice["session_token"] if practice else request.cookies.get("student_token")),
        get_client_ip(request), active=active, category=category,
    )


# ==============================================================================
# Health & Readiness Probes (P2)
# ==============================================================================
@app.get("/health")
def health_check():
    """Liveness probe: returns status 200 if FastAPI is accepting HTTP requests."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/ready")
def readiness_check():
    """Readiness probe: validates SQLite, PostgreSQL admin/student connections, and connection pool."""
    health = database.verify_system_health()
    is_ready = bool(health.get("sqlite") and health.get("postgres_admin") and health.get("postgres_student"))
    status_code = 200 if is_ready else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "degraded",
            "timestamp": datetime.now().isoformat(),
            "databases": {
                "sqlite": health.get("sqlite", False),
                "postgres_admin": health.get("postgres_admin", False),
                "postgres_student": health.get("postgres_student", False),
            },
            "checks": health.get("databases", {})
        }
    )

# ==============================================================================
# Pydantic Request Models
# ==============================================================================
class RunQueryRequest(BaseModel):
    nim: str
    session_token: str
    query: str
    active_database: Optional[str] = None

class SubmitAnswerRequest(BaseModel):
    nim: str
    session_token: str
    question_id: int
    query: str
    active_database: Optional[str] = None

class SelectCategoryRequest(BaseModel):
    nim: str
    session_token: str
    category: str

class HeartbeatRequest(BaseModel):
    nim: str
    session_token: str

class LogEventRequest(BaseModel):
    nim: str = Field(min_length=1, max_length=100)
    session_token: str = Field(min_length=1, max_length=256)
    action_type: str = Field(min_length=1, max_length=40)
    details: Optional[str] = Field(default=None, max_length=1000)

class SaveQuestionRequest(BaseModel):
    expected_edit_revision: Optional[int] = None
    question_id: Optional[int] = None
    category: str = "A"
    question_number: int = Field(ge=1, strict=True)
    title: str
    description: str
    score_weight: int = Field(default=15, strict=True, ge=1)
    expected_query: Optional[str] = None
    target_database: Optional[str] = "pagila"
    difficulty_level: Optional[str] = "Medium"
    expected_output_image: Optional[Union[str, List[str]]] = ""
    expected_output_images: Optional[List[str]] = None
    question_images: Optional[Union[str, List[str]]] = None
    grading_policy: Optional[Dict[str, Any]] = None
    setup_query: Optional[str] = None

class KickStudentRequest(BaseModel):
    nim: str

class ClearSessionsRequest(BaseModel):
    clear_logs: bool = False

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)

class CreatePackageRequest(BaseModel):
    code: str
    name: Optional[str] = ""
    description: Optional[str] = ""
    is_active: Optional[bool] = True

class UpdatePackageRequest(BaseModel):
    expected_edit_revision: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

# ==============================================================================
# Student Web Navigation Routes
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
def index_route(request: Request):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    client_ip = get_client_ip(request)

    if token and nim:
        is_valid, user, _ = database.validate_student_session(nim, token, client_ip)
        if is_valid:
            target_url = "/exam" if database.get_exam_state() == "active" else "/waiting"
            return RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)

    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None, message: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "message": message}
    )

@app.post("/login")
def handle_login(
    request: Request,
    nim: str = Form("", max_length=100),
    name: str = Form("", max_length=200),
    device_id: Optional[str] = Form(None, max_length=64)
):
    verify_csrf_origin(request)
    client_ip = get_client_ip(request)
    if (not rate_limiter.check(f"student_login_ip:{client_ip}", max_requests=120, window_seconds=60)
            or not rate_limiter.check(f"student_login_identity:{client_ip}:{nim.strip().lower()}", max_requests=6, window_seconds=60)):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Terlalu banyak percobaan login. Silakan tunggu sejenak.", "nim_value": nim, "name_value": name}, status_code=429
        )
    nim = nim.strip()
    name = name.strip()
    if not nim or not name:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "NIM dan Nama Lengkap wajib diisi!", "nim_value": nim, "name_value": name}
        )

    effective_device_id = (device_id or "").strip() or request.cookies.get("device_id") or str(uuid.uuid4())
    new_session_token = str(uuid.uuid4())

    success, user, error_msg = database.login_student(nim, name, client_ip, new_session_token, effective_device_id, resume_token=request.cookies.get("student_token"))

    if not success:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": error_msg, "nim_value": nim, "name_value": name}
        )

    target_url = "/exam" if database.get_exam_state() == "active" else "/waiting"
    response = RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)
    set_secure_cookie(response, "student_token", new_session_token, request)
    set_secure_cookie(response, "student_nim", nim, request)
    if effective_device_id:
        set_secure_cookie(response, "device_id", effective_device_id, request)
    return response

@app.get("/waiting", response_class=HTMLResponse)
def waiting_room_page(request: Request):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    client_ip = get_client_ip(request)

    if not token or not nim:
        return RedirectResponse(url="/login?error=Silakan+login+terlebih+dahulu", status_code=status.HTTP_302_FOUND)

    is_valid, user, reason = database.validate_student_session(nim, token, client_ip)
    if not is_valid:
        resp = RedirectResponse(url=f"/login?error={reason}", status_code=status.HTTP_302_FOUND)
        resp.delete_cookie("student_token")
        resp.delete_cookie("student_nim")
        return resp

    # If exam already active, send straight to exam
    if database.get_exam_state() == "active":
        return RedirectResponse(url="/exam", status_code=status.HTTP_302_FOUND)

    database.mark_student_waiting(nim, token, client_ip)
    return templates.TemplateResponse(
        request=request,
        name="waiting_room.html",
        context={"user": user, "exam_state": database.get_exam_state()}
    )

@app.get("/exam", response_class=HTMLResponse)
def exam_page(request: Request):
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

    # Guard: if exam not active, redirect back to waiting room
    if database.get_exam_state() != "active":
        return RedirectResponse(url="/waiting", status_code=status.HTTP_302_FOUND)

    # Mark student as 'online' (in exam) so they leave the waiting room list
    database.mark_student_in_exam(nim, token, client_ip)

    return templates.TemplateResponse(
        request=request,
        name="student.html",
        context={"user": user}
    )

@app.post("/logout")
def logout_route(request: Request, session_token: str = Form("", max_length=256)):
    token = request.cookies.get("student_token")
    nim = request.cookies.get("student_nim")
    if not token or not hmac.compare_digest(token.encode(), session_token.encode()):
        raise HTTPException(status_code=403, detail="Token logout mahasiswa tidak sesuai.")
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
def api_student_questions(request: Request):
    user = require_student(request, active=True)

    selected_cat = user.get("selected_category")
    if not selected_cat:
        available_categories = database.get_available_categories()
        return {
            "status": "needs_selection",
            "needs_category_selection": True,
            "category": None,
            "categories": available_categories,
            "questions": [],
            "submissions": {}
        }

    user = require_student(request, active=True, category=True)
    selected_cat = user["selected_category"]
    questions = database.get_questions_by_category(selected_cat)
    submissions = database.get_student_submissions(user["id"])
    return {
        "status": "success",
        "needs_category_selection": False,
        "category": selected_cat,
        "questions": questions,
        "submissions": submissions
    }

@app.post("/api/student/select-category")
def api_student_select_category(req: SelectCategoryRequest, request: Request):
    verify_csrf_origin(request)
    user = require_student(request, req.nim, req.session_token, active=True)
    ok, msg = database.lock_student_category(user["nim"], req.category, session_context=user)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    cat = req.category.strip().upper()
    database.log_exam_event(
        user["id"],
        "select_category",
        f"Mahasiswa mengunci pilihan Paket Soal: {cat}"
    )

    questions = database.get_questions_by_category(cat)
    return {
        "status": "success",
        "message": msg,
        "category": cat,
        "questions": questions,
        "submissions": database.get_student_submissions(user["id"])
    }

@app.get("/api/student/databases")
def api_student_databases(request: Request, nim: Optional[str] = None):
    user = require_student(request, active=True, category=True)
    if nim and nim.lower() != user["nim"].lower():
        raise HTTPException(status_code=403, detail="NIM tidak sesuai sesi Anda.")
    virtual_db = database.get_student_virtual_database(user["nim"])

    dbs = []
    for db_item in database.get_allowed_databases():
        d = dict(db_item)
        if d["name"] == "sandbox_pribadi" and virtual_db:
            d["label"] = f"sandbox_pribadi ({virtual_db})"
            d["virtual_db"] = virtual_db
        dbs.append(d)

    return {
        "status": "success",
        "default": database.DEFAULT_EXAM_DB,
        "databases": dbs,
        "virtual_db": virtual_db
    }

@app.post("/api/student/run-query")
def api_student_run_query(req: RunQueryRequest, request: Request):
    verify_csrf_origin(request)
    client_ip = get_client_ip(request)
    user = require_student(request, req.nim, req.session_token, active=True, category=True)

    if not rate_limiter.check(f"run_query:{user['nim']}:{client_ip}", max_requests=40, window_seconds=60):
        raise HTTPException(status_code=429, detail="Terlalu banyak eksekusi query. Silakan tunggu beberapa detik.")

    if len(req.query or "") > database.MAX_STUDENT_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail=f"Panjang query melebihi batas {database.MAX_STUDENT_QUERY_LENGTH} karakter.")

    active_db = (req.active_database or database.DEFAULT_EXAM_DB).strip().lower().split('(')[0].strip()
    if active_db not in database.ALLOWED_STUDENT_DBS:
        raise HTTPException(status_code=400, detail="Database target tidak valid.")

    # Execute inside PostgreSQL sandbox for the designated database and student NIM
    result = database.execute_student_query(req.query, active_db=active_db, nim=user["nim"])

    # Routine query logging removed to optimize SQLite performance and prevent log table bloat
    return result

@app.post("/api/student/submit")
def api_student_submit(req: SubmitAnswerRequest, request: Request):
    verify_csrf_origin(request)
    client_ip = get_client_ip(request)
    user = require_student(request, req.nim, req.session_token, active=True, category=True)

    if not rate_limiter.check(f"submit:{user['nim']}:{client_ip}", max_requests=25, window_seconds=60):
        raise HTTPException(status_code=429, detail="Terlalu banyak permintaan submit. Silakan tunggu beberapa detik.")

    if len(req.query or "") > database.MAX_STUDENT_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail=f"Panjang query melebihi batas {database.MAX_STUDENT_QUERY_LENGTH} karakter.")
    selected_cat = user.get("selected_category")
    if not selected_cat:
        raise HTTPException(status_code=400, detail="Anda belum memilih paket soal ujian!")

    # Guard: verify question exists and belongs to user's locked category
    question = database.get_question_by_id(req.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Soal tidak ditemukan.")

    if (question.get("category") or "A").upper() != selected_cat.upper():
        raise HTTPException(status_code=400, detail="Soal ini tidak sesuai dengan paket soal yang Anda pilih.")

    expected_query = question.get("expected_query") or ""
    score_weight = int(question["score_weight"])
    target_database = question.get("target_database") or database.DEFAULT_EXAM_DB
    active_database = (req.active_database or target_database).strip()

    # Auto-grade submission using table comparison and sandbox engine
    eval_res = database.evaluate_sql_submission(
        submitted_query=req.query,
        expected_query=expected_query,
        score_weight=score_weight,
        target_db=target_database,
        active_db=active_database,
        nim=user["nim"],
        grading_policy=question.get("grading_policy"), setup_query=question.get("setup_query", ""),
        question_id=question["id"]
    )

    is_correct_int = 1 if eval_res["is_correct"] else 0
    save_res = database.save_submission(
        user_id=user["id"],
        question_id=req.question_id,
        submitted_query=req.query,
        is_correct=is_correct_int,
        score=eval_res["score"],
        eval_status=eval_res["eval_status"],
        feedback=eval_res["feedback"],
        session_context={**user, "question_revision": question["question_revision"]},
        graded=eval_res.get("graded", True), active_database=active_database
    )

    effective_score = save_res["score"]
    effective_is_correct = save_res["is_correct"]
    score_retained = save_res.get("score_retained", False)

    student_res = eval_res.get("student_result") or {}
    return {
        "status": "success",
        "graded": eval_res.get("graded", True),
        "attempt_id": save_res["attempt_id"],
        "queue_status":save_res["queue_status"],
        "latest_attempt": save_res["latest_attempt"],
        "best_submission": save_res["best_submission"],
        "is_correct": effective_is_correct,
        "score": effective_score,
        "max_score": score_weight,
        "score_retained": score_retained,
        "previous_score": save_res.get("previous_score", 0),
        "new_score": save_res.get("new_score", 0),
        "retained_message": (
            f"Skor terbaik sebelumnya (+{effective_score} Poin) tetap aman dipertahankan."
            if score_retained else ""
        ),
        "eval_status": eval_res["eval_status"],
        "feedback": ("Jawaban tersimpan dan mengantre penilaian otomatis. Hasil akan diperbarui otomatis."
                     if save_res["queue_status"] == "queued" else eval_res["feedback"]),
        "target_database": target_database,
        "active_database": active_database,
        "columns": student_res.get("columns", []),
        "rows": student_res.get("rows", []),
        "row_count": student_res.get("row_count", 0),
        "preview_row_count": len(student_res.get("rows", [])),
        "is_truncated": student_res.get("is_truncated", False),
        "execution_time_ms": student_res.get("execution_time_ms", 0),
        "error": student_res.get("error")
    }

@app.post("/api/student/heartbeat")
def api_student_heartbeat(req: HeartbeatRequest, request: Request):
    try:
        user = require_student(request, req.nim, req.session_token)
        user = database.update_student_heartbeat(user["id"], req.session_token, get_client_ip(request))
    except database.StudentAccessError as error:
        return {"valid": False, "reason": str(error)}
    return {"valid": True, "selected_category": user.get("selected_category"),
            "exam_state": user["exam_state"], "exam_revision": user["exam_revision"]}

@app.post("/api/student/log-event")
def api_student_log_event(req: LogEventRequest, request: Request):
    user = require_student(request, req.nim, req.session_token, active=True, category=True)
    if req.action_type not in {"paste_attempt","devtools_attempt","blur_tab","focus_tab","exit_fullscreen"}:
        raise HTTPException(status_code=422, detail="Jenis peristiwa mahasiswa tidak diizinkan.")
    if not rate_limiter.check(f"student_log:{user['nim']}", 60, 60):
        raise HTTPException(status_code=429, detail="Terlalu banyak peristiwa mahasiswa.")
    database.log_student_event(user, req.action_type, req.details or "")
    return {"status":"ok"}

@app.get("/api/student/schema")
def api_student_schema(request: Request, db: Optional[str] = None):
    user = require_student(request, active=True, category=True)
    target_db = (db or database.DEFAULT_EXAM_DB).strip()
    nim = user["nim"]
    if target_db not in database.ALLOWED_STUDENT_DBS:
        raise HTTPException(status_code=400, detail="Database target tidak valid.")
    schema = database.get_database_schema(db_name=target_db, nim=nim)
    virtual_db = database.get_student_virtual_database(nim) if target_db == "sandbox_pribadi" else None
    return {"status": "success", "schema": schema, "database": target_db, "virtual_db": virtual_db}

@app.get("/api/student/schema-full")
def api_student_schema_full(request: Request, db: Optional[str] = None):
    user = require_student(request, active=True, category=True)
    nim = user["nim"]
    target_db = (db or database.DEFAULT_EXAM_DB).strip()
    if target_db not in database.ALLOWED_STUDENT_DBS:
        raise HTTPException(status_code=400, detail="Database target tidak valid.")
    schema = database.get_database_schema(db_name=target_db, nim=nim)
    relations = database.get_database_fk_relations(db_name=target_db, nim=nim)
    virtual_db = database.get_student_virtual_database(nim) if target_db == "sandbox_pribadi" else None
    return {
        "status": "success",
        "database": target_db,
        "virtual_db": virtual_db,
        "schema": schema,
        "relations": relations
    }

# ==============================================================================
# Admin & Lecturer Portal Routes
# ==============================================================================

def verify_admin_auth(request: Request, *, csrf=True) -> bool:
    token = request.cookies.get("admin_session")
    session = admin_security.get_session(token)
    if not session:
        return False
    request.state.admin_session = session
    if csrf and request.method in {"POST","PUT","PATCH","DELETE"}:
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied.encode(), admin_security.csrf_token(token).encode()):
            raise HTTPException(status_code=403, detail="Token CSRF admin tidak valid. Muat ulang halaman admin.")
    return True


@app.get("/admin/practice", response_class=HTMLResponse)
def admin_practice_page(request: Request):
    if not verify_admin_auth(request):
        return RedirectResponse('/admin/login',status_code=302)
    user,_ = admin_practice.prepare(request.state.admin_session,request.cookies['admin_session'],get_client_ip(request))
    return templates.TemplateResponse(request=request,name='student.html',context={
        'user':user,'admin_practice':True,'csrf_token':admin_security.csrf_token(request.cookies['admin_session'])})


@app.api_route('/api/admin/practice/{operation}',methods=['GET','POST'])
async def admin_practice_api(operation: str, request: Request):
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403,detail='Akses ditolak.')
    routes={
        'questions':('GET',api_student_questions,None),
        'databases':('GET',api_student_databases,None),
        'schema':('GET',api_student_schema,None),
        'schema-full':('GET',api_student_schema_full,None),
        'run-query':('POST',api_student_run_query,RunQueryRequest),
        'submit':('POST',api_student_submit,SubmitAnswerRequest),
        'select-category':('POST',api_student_select_category,SelectCategoryRequest),
        'heartbeat':('POST',api_student_heartbeat,HeartbeatRequest),
        'log-event':('POST',api_student_log_event,LogEventRequest),
    }
    if operation=='reset-package' and request.method=='POST':
        def reset_package(request):
            user=require_student(request)
            database.reset_student_category(user['nim'])
            return {'status':'success'}
        return await asyncio.to_thread(admin_practice.invoke,request.state.admin_session,request,reset_package)
    if operation not in routes:
        raise HTTPException(status_code=404,detail='Operasi tidak ditemukan.')
    method,handler,model=routes[operation]
    if request.method!=method:
        raise HTTPException(status_code=405,detail='Metode tidak diizinkan.')
    payload=None
    if model:
        try:
            payload=model.model_validate(await request.json())
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
        except ValueError as error:
            raise HTTPException(status_code=422,detail='JSON tidak valid.') from error
    query={}
    if operation in ('schema','schema-full'):
        query['db']=request.query_params.get('db')
    elif operation=='databases':
        query['nim']=request.query_params.get('nim')
    return await asyncio.to_thread(admin_practice.invoke,request.state.admin_session,request,handler,payload,query)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, error: Optional[str] = None):
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(request=request, name="admin_login.html", context={"error":error, "csrf_token":token})
    set_secure_cookie(response, "admin_login_csrf", token, request, max_age=600)
    return response


@app.post("/admin/login")
def admin_handle_login(request: Request, master_key: str = Form("", max_length=1024), csrf_token: str = Form("", max_length=128)):
    cookie = request.cookies.get("admin_login_csrf", "")
    if not cookie or not hmac.compare_digest(cookie.encode(), csrf_token.encode()):
        raise HTTPException(status_code=403, detail="Form login kedaluwarsa. Muat ulang halaman login.")
    client_ip = get_client_ip(request)
    if not rate_limiter.check(f"admin_login:{client_ip}", max_requests=6, window_seconds=30):
        raise HTTPException(status_code=429, detail="Terlalu banyak percobaan login. Tunggu 30 detik.")
    token = admin_security.login(master_key.strip(), client_ip, request.cookies.get("admin_session"))
    if not token:
        return RedirectResponse(url="/admin/login?error=Master+Key+salah!", status_code=303)
    res = RedirectResponse(url="/admin", status_code=303)
    set_secure_cookie(res, "admin_session", token, request)
    res.delete_cookie("admin_login_csrf")
    request.state.admin_session = admin_security.get_session(token)
    return res

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    if not verify_admin_auth(request):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

    host_ip = get_local_host_ip()
    is_default_master_key = (admin_security.check_key("admin123", database.get_admin_master_key()))
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"host_ip": host_ip, "is_default_master_key": is_default_master_key, "csrf_token":admin_security.csrf_token(request.cookies["admin_session"])}
    )

@app.post("/admin/logout")
def admin_logout(request: Request, csrf_token: str = Form("")):
    if not verify_admin_auth(request, csrf=False):
        raise HTTPException(status_code=403, detail="Sesi admin tidak valid.")
    supplied = request.headers.get("x-csrf-token") or csrf_token
    if not hmac.compare_digest(supplied.encode(), admin_security.csrf_token(request.cookies["admin_session"]).encode()):
        raise HTTPException(status_code=403, detail="Token CSRF tidak valid.")
    admin_security.logout(request.cookies["admin_session"])
    res = RedirectResponse(url="/admin/login", status_code=303)
    res.delete_cookie("admin_session")
    return res

# ------------------------------------------------------------------------------
# Admin REST APIs
# ------------------------------------------------------------------------------

@app.get("/api/admin/diagnostics")
def api_admin_diagnostics(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    import query_runner
    import grading_engine
    return {"health":database.verify_system_health(), "pool":database.get_pool_stats(),
            "limits":{"sandbox_connections_per_process":query_runner.SANDBOX_MAX_CONNECTIONS,
                      "grading_slots_per_process":4, "query_seconds":query_runner.QUERY_BUDGET_SECONDS,
                      "grading_seconds":grading_engine.GRADE_SECONDS}}


class RegradeRequest(BaseModel):
    question_revision: int = Field(ge=1, strict=True)
    expected_decision_id: int = Field(ge=0, strict=True)


class ManualGradeRequest(RegradeRequest):
    score: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=2000)


@app.get("/api/admin/grading/jobs")
def api_admin_grading_jobs(request: Request, status: Optional[str] = None, before_id: Optional[int] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    if status and status not in {"queued","running","review","completed","stale","cancelled"}:
        raise HTTPException(status_code=422, detail="Status antrean tidak valid.")
    with closing(database.get_sqlite_conn()) as conn:
        counts = {row[0]:row[1] for row in conn.execute("SELECT status,COUNT(*) FROM grading_jobs GROUP BY status")}
        rows = conn.execute("""SELECT j.*,u.nim,a.question_id,q.question_revision AS current_question_revision,
            (SELECT COALESCE(MAX(id),0) FROM grading_decisions WHERE attempt_id=a.id) AS decision_id
            FROM grading_jobs j JOIN submission_attempts a ON a.id=j.attempt_id JOIN users u ON u.id=a.user_id
            JOIN questions q ON q.id=a.question_id WHERE (? IS NULL OR j.status=?) AND (? IS NULL OR j.id<?)
            ORDER BY j.id DESC LIMIT 100""", (status,status,before_id,before_id)).fetchall()
        maintenance = bool(grading_queue.setting(conn,"grading_maintenance",""))
        dataset_revision = int(grading_queue.setting(conn,"dataset_revision"))
    jobs = [dict(row) for row in rows]
    for job in jobs:
        job.pop("lease_token",None)
    worker = getattr(app.state,"grading_worker",None)
    return {"jobs":jobs,"counts":counts,"dataset_maintenance":maintenance,"dataset_revision":dataset_revision,
            "worker":{"running":bool(worker and worker.thread and worker.thread.is_alive()),"last_error":worker.last_error if worker else None},
            "next_before_id":jobs[-1]["id"] if len(jobs)==100 else None}


@app.get("/api/admin/grading/attempts/{attempt_id}")
def api_admin_grading_attempt(attempt_id: int, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    with closing(database.get_sqlite_conn()) as conn:
        attempt = conn.execute("SELECT * FROM submission_attempts WHERE id=?",(attempt_id,)).fetchone()
        if not attempt:
            raise HTTPException(status_code=404,detail="Percobaan tidak ditemukan.")
        question = conn.execute("SELECT id,title,score_weight,question_revision FROM questions WHERE id=?",(attempt["question_id"],)).fetchone()
        effective = grading_queue.effective_attempt(conn,attempt)
        decisions = [dict(row) for row in conn.execute("SELECT * FROM grading_decisions WHERE attempt_id=? ORDER BY id DESC LIMIT 100",(attempt_id,))]
    for decision in decisions:
        decision.pop("run_token",None)
    return {"attempt":effective,"question":dict(question),"decisions":decisions}


@app.post("/api/admin/grading/attempts/{attempt_id}/manual")
def api_admin_grade_manual(attempt_id: int, req: ManualGradeRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    decision_id = grading_queue.manual(attempt_id,req.question_revision,req.expected_decision_id,req.score,req.reason,request.state.admin_session["id"])
    return {"status":"success","decision_id":decision_id}


@app.post("/api/admin/grading/attempts/{attempt_id}/regrade")
def api_admin_regrade(attempt_id: int, req: RegradeRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    job_id = grading_queue.regrade(attempt_id,req.question_revision,req.expected_decision_id,request.state.admin_session["id"])
    return {"status":"success","job_id":job_id}


@app.get("/api/admin/session")
def api_admin_session(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Sesi admin tidak valid.")
    actor = request.state.admin_session
    return {"session_id":actor["id"], "expires_at":actor["expires_at"], "csrf_token":admin_security.csrf_token(request.cookies["admin_session"])}


@app.get("/api/admin/audit")
def api_admin_audit(request: Request, before_id: Optional[int] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    with closing(database.get_sqlite_conn()) as conn:
        rows = conn.execute("SELECT * FROM admin_audit WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT 100", (before_id,before_id)).fetchall()
    records = [dict(row) for row in rows]
    return {"records":records, "next_before_id":records[-1]["id"] if len(records)==100 else None}


@app.get("/api/admin/students")
def api_admin_students(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    students = database.get_all_students_progress()
    return {"students": students}

@app.get("/api/admin/students/{nim}/attempts")
def api_admin_student_attempts(nim: str, request: Request, before_id: Optional[int] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    with closing(database.get_sqlite_conn()) as conn:
        rows = conn.execute("""SELECT a.* FROM submission_attempts a JOIN users u ON u.id=a.user_id
            WHERE LOWER(u.nim)=LOWER(?) AND (? IS NULL OR a.id<?) ORDER BY a.id DESC LIMIT 100""",
            (nim, before_id, before_id)).fetchall()
    attempts = [dict(row) for row in rows]
    return {"attempts": attempts, "next_before_id": attempts[-1]["id"] if len(attempts) == 100 else None}


@app.post("/api/admin/kick-student")
def api_admin_kick_student(req: KickStudentRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    if not database.kick_student(req.nim):
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    return {"status": "success", "message": "Sesi dihentikan. Bebaskan sesi untuk mengizinkan login kembali."}

@app.post("/api/admin/clear-session-lock")
def api_admin_clear_session_lock(req: KickStudentRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    success, msg = database.clear_student_session_lock(req.nim)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}

@app.post("/api/admin/stop-all-sessions")
def api_admin_stop_all_sessions(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    count = database.stop_all_active_sessions()
    return {"status": "success", "message": f"Semua sesi aktif telah dihentikan ({count} sesi dinonaktifkan)."}

@app.post("/api/admin/clear-logs")
def api_admin_clear_logs(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    count = database.clear_exam_logs()
    return {"status": "success", "message": f"Log aktivitas berhasil dibersihkan ({count} entri dihapus)."}

@app.post("/api/admin/clear-sessions")
def api_admin_clear_sessions(req: ClearSessionsRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    result = database.clear_previous_sessions(clear_logs=req.clear_logs)
    return {
        "status": "success",
        "message": f"Data sesi sebelumnya berhasil dibersihkan. {result['users_cleared']} mahasiswa dan {result['submissions_cleared']} submisi dihapus."
    }

@app.post("/api/admin/change-password")
def api_admin_change_password(req: ChangePasswordRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    new_pw = req.new_password.strip()
    if len(new_pw) < 12 or len(new_pw) > 1024:
        raise HTTPException(status_code=400, detail="Master key baru harus 12–1.024 karakter.")
    token = admin_security.change_key(req.current_password.strip(), new_pw, request.cookies["admin_session"], get_client_ip(request))
    if not token:
        raise HTTPException(status_code=400, detail="Password lama atau sesi tidak sesuai.")
    res = JSONResponse(content={"status":"success", "success":True, "message":"Master key diperbarui. Sesi admin lain dicabut; muat ulang halaman ini."})
    set_secure_cookie(res, "admin_session", token, request)
    return res

@app.get("/api/admin/questions")
def api_admin_questions(request: Request, category: Optional[str] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    questions = database.get_all_questions(include_expected_query=True, category=category)
    categories = database.get_available_categories()
    packages = database.get_all_packages()
    for package in packages:
        package['edit_revision']=database.package_revision(package)
    return {"status": "success", "questions": questions, "categories": categories, "packages": packages}

@app.post("/api/admin/questions")
def api_admin_save_question(req: SaveQuestionRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    if database.get_exam_state() == "active":
        raise HTTPException(status_code=409, detail="Akhiri ujian sebelum mengubah soal atau kebijakan penilaian.")
    try:
        from grading_results import policy_from
        current = database.get_question_by_id(req.question_id) if req.question_id else {}
        policy = policy_from(req.grading_policy if req.grading_policy is not None else (current or {}).get("grading_policy"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Kebijakan penilaian tidak valid. Periksa format dan nilai grading_policy.") from error
    from grading_engine import reference_statements
    from sql_policy import SQLPolicyError, ParseError
    def validate_sql(text,sandbox,label):
        try:
            reference_statements(text,sandbox)
        except (ValueError,SQLPolicyError,ParseError) as error:
            reason=str(error).replace('\n',' ')[:300]
            raise HTTPException(status_code=400,detail=f"{label} bukan SQL yang valid atau diizinkan: {reason}") from error
    if policy.mode == 'automatic':
        validate_sql(req.expected_query or '',(req.target_database or database.DEFAULT_EXAM_DB)=='sandbox_pribadi','Kunci jawaban')
    if req.setup_query:
        validate_sql(req.setup_query,True,'Query prasyarat (setup)')

    exp_imgs = req.expected_output_images if req.expected_output_images is not None else req.expected_output_image
    q_imgs = req.question_images if req.question_images is not None else ""

    if req.question_id and req.expected_edit_revision is None:
        raise HTTPException(status_code=409, detail="Muat ulang soal untuk mendapatkan revisi edit terbaru.")
    q_id = database.save_question(
        question_number=req.question_number,
        title=req.title,
        description=req.description,
        score_weight=req.score_weight,
        expected_query=req.expected_query,
        category=req.category,
        question_id=req.question_id,
        target_database=req.target_database or database.DEFAULT_EXAM_DB,
        difficulty_level=req.difficulty_level or "Medium",
        expected_output_image=exp_imgs or "",
        question_images=q_imgs or "",
        grading_policy=req.grading_policy, setup_query=req.setup_query, expected_edit_revision=req.expected_edit_revision
    )
    return {
        "status": "success",
        "id": q_id,
        "message": f"Soal Paket {req.category} #{req.question_number} berhasil disimpan."
    }

@app.post("/api/admin/questions/upload-images")
async def api_admin_upload_question_images(
    request: Request,
    images: List[UploadFile] = File(...),
    image_type: Optional[str] = Form("question")
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    import question_uploads
    urls=await question_uploads.store(images,STATIC_DIR,(image_type or '').lower())
    return {'status':'success','image_urls':urls,'image_url':urls[0],'count':len(urls)}


@app.post("/api/admin/questions/upload-image")
async def api_admin_upload_question_image(request: Request,image: UploadFile=File(...),image_type: Optional[str]=Form('output')):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403,detail='Akses ditolak')
    import question_uploads
    urls=await question_uploads.store([image],STATIC_DIR,(image_type or '').lower())
    return {'status':'success','image_url':urls[0],'filename':image.filename}


@app.post("/api/admin/student/{nim}/reset-category")
def api_admin_reset_student_category(nim: str, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    ok, msg = database.reset_student_category(nim)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}

@app.delete("/api/admin/questions/{question_id}")
def api_admin_delete_question(question_id: int, request: Request, expected_edit_revision: Optional[int] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    if expected_edit_revision is None:
        raise HTTPException(status_code=409, detail="Revisi edit diperlukan untuk menghapus soal.")
    database.delete_question(question_id, expected_edit_revision)
    return {"status": "success", "message": "Soal ujian berhasil dihapus."}

@app.delete("/api/admin/questions")
def api_admin_delete_all_questions(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    database.delete_all_questions()
    return {"status": "success", "message": "Seluruh soal ujian berhasil dihapus."}

# ------------------------------------------------------------------------------
# Exam Package Management APIs
# ------------------------------------------------------------------------------

@app.get("/api/admin/packages")
def api_admin_packages(request: Request, include_inactive: bool = True):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    packages = database.get_all_packages(include_inactive=include_inactive)
    for package in packages:
        package['edit_revision']=database.package_revision(package)
    return {"status": "success", "packages": packages}

@app.post("/api/admin/packages")
def api_admin_create_package(req: CreatePackageRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    code = req.code.strip().upper()
    name = (req.name or f"Paket {code}").strip()
    desc = (req.description or "").strip()
    is_act = 1 if req.is_active else 0

    ok, msg, new_id = database.create_package(
        code=code,
        name=name,
        description=desc,
        is_active=is_act, guard_exam=True
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    database.log_exam_event(
        None,
        "create_package",
        f"Pengawas membuat paket soal baru: {code} ({name})"
    )
    return {
        "status": "success",
        "message": msg,
        "id": new_id,
        "package": database.get_package_by_code(code)
    }

@app.put("/api/admin/packages/{code}")
def api_admin_update_package(code: str, req: UpdatePackageRequest, request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    is_act = (1 if req.is_active else 0) if req.is_active is not None else None
    if not database.get_package_by_code(code.strip().upper()):
        raise HTTPException(status_code=404,detail='Paket tidak ditemukan.')
    if req.expected_edit_revision is None:
        raise HTTPException(status_code=409,detail='Muat ulang paket untuk mendapatkan revisi terbaru.')
    ok, msg = database.update_package(
        code=code,
        name=req.name,
        description=req.description,
        is_active=is_act, expected_revision=req.expected_edit_revision
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    database.log_exam_event(
        None,
        "update_package",
        f"Pengawas memperbarui paket soal {code.strip().upper()}"
    )
    return {
        "status": "success",
        "message": msg,
        "package": database.get_package_by_code(code)
    }

@app.delete("/api/admin/packages/{code}")
def api_admin_delete_package(code: str, request: Request, delete_questions: bool = False, expected_edit_revision: Optional[str] = None):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    if not database.get_package_by_code(code.strip().upper()):
        raise HTTPException(status_code=404,detail='Paket tidak ditemukan.')
    if expected_edit_revision is None:
        raise HTTPException(status_code=409,detail='Muat ulang paket untuk mendapatkan revisi terbaru.')
    ok, msg = database.delete_package(code, delete_questions=delete_questions,expected_revision=expected_edit_revision)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    database.log_exam_event(
        None,
        "delete_package",
        f"Pengawas menghapus paket soal {code.strip().upper()} (delete_questions={delete_questions})"
    )
    return {"status": "success", "message": msg}

# ==============================================================================
# Exam State & Waiting Room API
# ==============================================================================

@app.get("/api/exam/state")
def api_exam_state(request: Request):
    user = require_student(request)
    return {"state": user["exam_state"], "revision": user["exam_revision"]}

@app.post("/api/admin/exam/start")
def api_admin_exam_start(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak.")
    try:
        database.start_exam_validated()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"status": "success", "message": "Ujian dimulai. Seluruh peserta akan diarahkan ke lembar soal."}


@app.post("/api/admin/exam/validate")
def api_admin_exam_validate(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak.")
    try:
        readiness = database.validate_exam_readiness()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"status": "success", "message": "Validasi kesiapan selesai. Ujian siap dimulai.", "readiness": readiness}

@app.post("/api/admin/exam/stop")
def api_admin_exam_stop(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak.")
    database.set_exam_state("finished")
    return {"status": "success", "state": "finished", "message": "Ujian selesai. Akses soal dan perubahan jawaban ditutup."}

@app.get("/api/waiting/participants")
def api_waiting_participants(request: Request):
    require_student(request)
    participants = database.get_waiting_room_participants()
    return {"participants": participants, "count": len(participants)}


@app.post("/api/waiting/heartbeat")
def api_waiting_heartbeat(request: Request):
    user = require_student(request)
    database.heartbeat_student_waiting(user["nim"], user["session_token"], get_client_ip(request))
    return {"ok": True}

@app.get("/api/admin/waiting/participants")
def api_admin_waiting_participants(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak.")
    participants = database.get_waiting_room_participants()
    exam_state = database.get_exam_state()
    return {"participants": participants, "count": len(participants), "exam_state": exam_state,
            "readiness": database.get_exam_readiness()}



@app.get("/api/admin/schema")
def api_admin_schema(request: Request, db: Optional[str] = "pagila"):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    target_db = (db or "pagila").strip().lower()
    schema = database.get_database_schema(db_name=target_db)
    relations = database.get_database_fk_relations(db_name=target_db)
    return {
        "status": "success",
        "database": target_db,
        "schema": schema,
        "relations": relations,
        "available_databases": database.get_allowed_databases()
    }

@app.get("/api/admin/table-data")
def api_admin_table_data(request: Request, db: Optional[str] = "pagila", table: str = "", limit: int = 50, offset: int = 0):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    target_db = (db or "pagila").strip().lower()
    if target_db not in database.ALLOWED_MASTER_DBS:
        raise HTTPException(status_code=400, detail="Target database tidak valid.")
    if not table:
        raise HTTPException(status_code=400, detail="Parameter table diperlukan.")
    try:
        data = database.get_table_data(db_name=target_db, table_name=table, limit=limit, offset=offset)
        return {"status": "success", **data}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memuat data tabel: {e}")

class ResetDatabaseAdminRequest(BaseModel):
    target_db: Optional[str] = "classicmodels"

@app.post("/api/admin/reset-database")
def api_admin_reset_database(request: Request, req: Optional[ResetDatabaseAdminRequest] = None):
    verify_csrf_origin(request)
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    client_ip = get_client_ip(request)
    if not rate_limiter.check(f"reset_db:{client_ip}", max_requests=6, window_seconds=60):
        raise HTTPException(status_code=429, detail="Terlalu banyak permintaan reset database. Silakan tunggu.")

    target_db = (req.target_db if req and req.target_db else "classicmodels").strip().lower()
    if target_db not in database.ALLOWED_MASTER_DBS:
        raise HTTPException(status_code=400, detail="Target database tidak valid.")
    success, msg = database.reset_target_database(target_db=target_db)
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    database.log_exam_event(None, "reset_database", f"Pengawas me-reset database '{target_db}' ke kondisi awal.")
    return {"status": "success", "message": msg}

@app.post("/api/admin/upload-sql")
async def api_admin_upload_sql(
    request: Request,
    file: UploadFile = File(...),
    target_db: Optional[str] = Form("classicmodels")
):
    verify_csrf_origin(request)
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    client_ip = get_client_ip(request)
    if not rate_limiter.check(f"upload_sql:{client_ip}", max_requests=6, window_seconds=60):
        raise HTTPException(status_code=429, detail="Terlalu banyak permintaan unggah SQL. Silakan tunggu.")

    MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB limit
    content_bytes = await file.read(MAX_UPLOAD_SIZE + 1024)
    if len(content_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Ukuran file SQL melebihi batas maksimal (25 MB).")

    try:
        sql_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        sql_text = content_bytes.decode("latin-1")

    db_choice = (target_db or "classicmodels").strip().lower()
    if db_choice not in database.ALLOWED_MASTER_DBS:
        raise HTTPException(status_code=400, detail="Target database tidak valid.")
    success, msg = await asyncio.to_thread(database.reset_target_database, target_db=db_choice, custom_sql_text=sql_text)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    database.log_exam_event(None, "upload_sql", f"Pengawas mengunggah custom dump SQL ke database '{db_choice}' ({len(sql_text)} karakter).")
    return {"status": "success", "message": msg}

@app.get("/api/admin/logs")
def api_admin_logs(request: Request, limit: int = 50, offset: int = 0):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")
    logs, total = database.get_recent_exam_logs(limit=limit, offset=offset)
    return {"logs": logs, "total": total, "limit": limit, "offset": offset}

@app.get("/api/admin/export/csv")
def api_admin_export_csv(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    csv_data = database.export_all_submissions_csv()
    csv_bytes = csv_data.encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="hasil_ujian_praktikum.csv"',
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Length": str(len(csv_bytes)),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/api/admin/export/zip")
def api_admin_export_zip(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    zip_bytes = database.export_submissions_zip_bytes()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="jawaban_sql_mahasiswa.zip"',
            "Content-Type": "application/zip",
            "Content-Length": str(len(zip_bytes)),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/api/admin/export/logs-csv")
def api_admin_export_logs_csv(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    csv_data = database.export_all_logs_csv()
    csv_bytes = csv_data.encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="rekap_log_aktivitas.csv"',
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Length": str(len(csv_bytes)),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/api/admin/export/tab-switches-csv")
def api_admin_export_tab_switches_csv(request: Request):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=403, detail="Akses ditolak")

    csv_data = database.export_tab_switches_csv()
    csv_bytes = csv_data.encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="rekap_perpindahan_tab.csv"',
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Length": str(len(csv_bytes)),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


# ==============================================================================
# Main Runner Entry Point
# ==============================================================================
if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so clients on local router Wi-Fi can connect
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
