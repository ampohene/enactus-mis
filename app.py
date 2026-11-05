"""Enactus MIS - members, meetings/attendance, projects. Flask + SQLite.

Copyright (c) 2026 ampohene (or the name set in BUILT_BY). All rights reserved; see LICENSE.txt.
"""
import csv, functools, hashlib, html, io, json, os, secrets, smtplib, sqlite3, ssl, time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
import click
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
try:  # tiny .env loader (no extra dependency)
    for _l in open(os.path.join(BASE, ".env"), encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())
except FileNotFoundError:
    pass
UPLOAD_DIR = os.environ.get("MIS_UPLOADS", os.path.join(BASE, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg", "txt", "csv"}
DB_PATH = os.environ.get("MIS_DB", os.path.join(BASE, "mis.db"))
app = Flask(__name__)
app.jinja_env.finalize = lambda v: "" if v is None else v  # empty fields show blank, not "None"
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or "dev-only-change-me",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PRODUCTION") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
)
GOOGLE_ON = bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
oauth = None
if GOOGLE_ON:
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    oauth.register("google", client_id=os.environ["GOOGLE_CLIENT_ID"],
                   client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile"})
if os.environ.get("PRODUCTION") == "1" and app.config["SECRET_KEY"] == "dev-only-change-me":
    raise SystemExit("Set the SECRET_KEY environment variable in production.")

ORG = os.environ.get("ORG_NAME", "Enactus UTAS")
BUILT_BY = os.environ.get("BUILT_BY", "ampohene")  # shown as "System by ..." in the app, receipts and emails
CURRENCY = os.environ.get("CURRENCY", "GHS")
APPROVAL_MODE = os.environ.get("APPROVAL_MODE", "both")  # "both" = President AND Vice President; "either" = one of them
ROLES = {"president": "President", "vice_president": "Vice President", "pro": "P.R.O",
         "project_manager": "Project Manager", "finance_director": "Finance Director",
         "secretary": "Secretary", "member": "Member"}
TOP = {"president", "vice_president"}  # run everything; their own changes apply at once
PERMS = {  # what each portfolio may change (President and Vice President can do all of it)
    "members_view": {"secretary"}, "members_edit": {"secretary"},
    "attendance_view": {"secretary", "pro"}, "attendance_edit": {"secretary"},
    "meetings_manage": {"pro"}, "announce": {"pro"},
    "projects_manage": {"project_manager"}, "budget_view": {"project_manager", "finance_director"},
    "finance": {"finance_director"},
    "reports": {"secretary", "pro", "project_manager", "finance_director"},
}
PAY_KINDS = ["Dues", "Event fee", "Donation", "Other"]
PAY_METHODS = ["Cash", "Mobile Money", "Bank transfer", "Other"]
STATUSES = ["Planning", "Ongoing", "Paused", "Completed"]
ATT = ["present", "late", "excused", "absent"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  phone TEXT, department TEXT, level TEXT, position TEXT DEFAULT 'Member',
  role TEXT NOT NULL DEFAULT 'member', pw_hash TEXT, active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS meetings(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, held_on TEXT NOT NULL, location TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS attendance(
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  member_id INTEGER NOT NULL REFERENCES members(id),
  present INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(meeting_id, member_id));
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Planning',
  summary TEXT, start_date TEXT, end_date TEXT, budget REAL DEFAULT 0, impact TEXT,
  lead_id INTEGER REFERENCES members(id));
CREATE TABLE IF NOT EXISTS project_members(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  member_id INTEGER NOT NULL REFERENCES members(id), PRIMARY KEY(project_id, member_id));
CREATE TABLE IF NOT EXISTS project_files(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, stored TEXT NOT NULL, size INTEGER, by TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS resets(
  token_hash TEXT PRIMARY KEY, member_id INTEGER NOT NULL REFERENCES members(id), expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, description TEXT);
CREATE TABLE IF NOT EXISTS member_units(
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE, PRIMARY KEY(member_id, unit_id));
CREATE TABLE IF NOT EXISTS announcements(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT, author TEXT, at TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS project_editors(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  member_id INTEGER NOT NULL REFERENCES members(id), PRIMARY KEY(project_id, member_id));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, receipt_no TEXT UNIQUE, member_id INTEGER NOT NULL REFERENCES members(id),
  kind TEXT NOT NULL, description TEXT, amount REAL NOT NULL, paid_on TEXT NOT NULL, method TEXT, note TEXT,
  recorded_by TEXT, created_at TEXT, voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT,
  emailed_at TEXT, email_status TEXT);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT, payload TEXT NOT NULL, summary TEXT NOT NULL, details TEXT,
  by_id INTEGER NOT NULL REFERENCES members(id), at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  decided_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  change_id INTEGER NOT NULL REFERENCES changes(id) ON DELETE CASCADE,
  approver_id INTEGER NOT NULL REFERENCES members(id), role TEXT NOT NULL, decision TEXT NOT NULL,
  note TEXT, at TEXT NOT NULL, PRIMARY KEY(change_id, approver_id));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT, action TEXT NOT NULL);
"""

# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()

def init_db():
    d = sqlite3.connect(DB_PATH)
    d.executescript(SCHEMA)
    def cols(t): return {r[1] for r in d.execute(f"PRAGMA table_info({t})")}
    for c in ("student_id", "date_joined", "emergency_name", "emergency_phone"):
        if c not in cols("members"):
            d.execute(f"ALTER TABLE members ADD COLUMN {c} TEXT")
    if "status" not in cols("attendance"):
        d.execute("ALTER TABLE attendance ADD COLUMN status TEXT")
    d.execute("UPDATE members SET role='president' WHERE role='admin'")
    d.execute("UPDATE members SET role='member' WHERE role='exec'")
    d.execute("UPDATE attendance SET status=CASE present WHEN 1 THEN 'present' ELSE 'absent' END WHERE status IS NULL")
    d.commit()
    d.close()

def log(action):
    who = g.user["email"] if g.get("user") else "-"
    db().execute("INSERT INTO audit(at,who,action) VALUES(?,?,?)",
                 (datetime.now().isoformat(timespec="seconds"), who, action))

# ---------- auth / security ----------
@app.before_request
def load_user():
    g.user = None
    if session.get("uid"):
        g.user = db().execute("SELECT * FROM members WHERE id=? AND active=1",
                              (session["uid"],)).fetchone()
        if not g.user:
            session.clear()
    if request.method == "POST":
        tok = session.get("csrf")
        if not tok or not secrets.compare_digest(tok, request.form.get("csrf", "")):
            abort(400, "Invalid form token. Reload the page and try again.")

@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return dict(csrf=session["csrf"], user=g.get("user"), ATT=ATT, google_on=GOOGLE_ON, can=can, is_top=is_top,
                ROLES=ROLES, awaiting=pending_count(), ORG=ORG, CURRENCY=CURRENCY, built_by=BUILT_BY)

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Cache-Control"] = "no-store"
    r.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    return r

def need(perm=None, top=False):
    def deco(f):
        @functools.wraps(f)
        def w(*a, **k):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if g.user["must_change"] and not session.get("google") and request.endpoint not in ("password", "logout"):
                return redirect(url_for("password"))
            if (top and not is_top()) or (perm and not can(perm)):
                abort(403)
            return f(*a, **k)
        return w
    return deco

FAILS = {}  # (ip, email) -> (count, last_time); resets on restart

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = (request.remote_addr, email)
        n, t = FAILS.get(key, (0, 0))
        if n >= 5 and time.time() - t < 600:
            flash("Too many failed attempts. Try again in 10 minutes.")
            return render_template("login.html", google=GOOGLE_ON), 429
        m = db().execute("SELECT * FROM members WHERE email=? AND active=1", (email,)).fetchone()
        if m and m["pw_hash"] and check_password_hash(m["pw_hash"], request.form.get("password", "")):
            FAILS.pop(key, None)
            sign_in(m)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        FAILS[key] = (n + 1, time.time())
        flash("Email or password is incorrect.")
    return render_template("login.html", google=GOOGLE_ON)

def sign_in(m, via_google=False):
    session.clear()
    session.permanent = True
    session["uid"] = m["id"]
    session["csrf"] = secrets.token_hex(16)
    if via_google:
        session["google"] = True
    g.user = m
    log("login with Google" if via_google else "login")
    db().commit()

@app.route("/auth/google")
def google_start():
    if not GOOGLE_ON:
        abort(404)
    try:
        return oauth.google.authorize_redirect(url_for("google_callback", _external=True))
    except Exception:
        flash("Could not reach Google. Check your internet connection and try again.")
        return redirect(url_for("login"))

@app.route("/auth/google/callback")
def google_callback():
    if not GOOGLE_ON:
        abort(404)
    try:
        info = oauth.google.authorize_access_token().get("userinfo") or {}
    except Exception:
        flash("Google sign-in failed. Please try again.")
        return redirect(url_for("login"))
    email = (info.get("email") or "").lower()
    m = db().execute("SELECT * FROM members WHERE email=? AND active=1", (email,)).fetchone() if info.get("email_verified") else None
    if not m:
        flash("That Google account is not registered. Ask an executive to add your email first.")
        return redirect(url_for("login"))
    sign_in(m, True)
    return redirect(url_for("home"))

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    h = hashlib.sha256(token.encode()).hexdigest()
    r = db().execute("SELECT * FROM resets WHERE token_hash=?", (h,)).fetchone()
    if not r or r["expires"] < datetime.now().isoformat():
        flash("This reset link is invalid or has expired. Ask the President for a new one.")
        return redirect(url_for("login"))
    if request.method == "POST":
        new = request.form.get("new", "")
        if len(new) < 10:
            flash("Password must be at least 10 characters.")
        else:
            db().execute("UPDATE members SET pw_hash=?, must_change=0 WHERE id=?", (generate_password_hash(new), r["member_id"]))
            db().execute("DELETE FROM resets WHERE member_id=?", (r["member_id"],))
            db().commit()
            flash("Password set. You can sign in now.")
            return redirect(url_for("login"))
    return render_template("reset.html")

RESET_ASKS = {}  # rate limit for "forgot password": key -> list of timestamps

def _too_many(key, limit, window=900):
    t = time.time()
    hits = [x for x in RESET_ASKS.get(key, []) if t - x < window]
    RESET_ASKS[key] = hits + [t]
    return len(hits) >= limit

def forgot_ready():
    """Self-service reset needs email, and (when live) a fixed BASE_URL so links can't be forged."""
    return smtp_ready() and (os.environ.get("PRODUCTION") != "1" or bool(os.environ.get("BASE_URL")))

@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    ready = forgot_ready()
    if request.method == "POST" and ready:
        email = request.form.get("email", "").strip().lower()
        if not _too_many(("ip", request.remote_addr), 10) and not _too_many(("em", email), 3):
            m = db().execute("SELECT * FROM members WHERE email=? AND active=1", (email,)).fetchone()
            if m:
                tok = secrets.token_urlsafe(32)
                db().execute("DELETE FROM resets WHERE member_id=?", (m["id"],))
                db().execute("INSERT INTO resets VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), m["id"],
                             (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")))
                base = os.environ.get("BASE_URL", "").rstrip("/")
                link = (base + url_for("reset", token=tok)) if base else url_for("reset", token=tok, _external=True)
                msg = EmailMessage()
                msg["Subject"] = f"Reset your {ORG} MIS password"
                msg["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
                msg["To"] = m["email"]
                msg.set_content(f"Hello {m['name']},\n\nSomeone asked to reset the password for your {ORG} MIS account. "
                                f"Open this link within 1 hour to choose a new password:\n\n{link}\n\n"
                                "If you did not ask for this, ignore this email. Your password stays the same.")
                try:
                    smtp_send(msg)
                except Exception:
                    pass
                db().execute("INSERT INTO audit(at,who,action) VALUES(?,?,?)", (now(), "-", f"password reset requested for {email}"))
                db().commit()
        return render_template("forgot.html", ready=True, sent=True)
    return render_template("forgot.html", ready=ready, sent=False)

@app.route("/members/<int:mid>/reset", methods=["POST"])
@need(top=True)
def make_reset(mid):
    m = db().execute("SELECT email FROM members WHERE id=?", (mid,)).fetchone() or abort(404)
    tok = secrets.token_urlsafe(32)
    db().execute("DELETE FROM resets WHERE member_id=?", (mid,))
    db().execute("INSERT INTO resets VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), mid,
                 (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")))
    log(f"created reset link for {m['email']}")
    db().commit()
    session["reset_link"] = url_for("reset", token=tok, _external=True)
    return redirect(url_for("member_form", mid=mid))

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/password", methods=["GET", "POST"])
@need()
def password():
    if request.method == "POST":
        cur, new = request.form.get("current", ""), request.form.get("new", "")
        if g.user["pw_hash"] and not check_password_hash(g.user["pw_hash"], cur):
            flash("Current password is incorrect.")
        elif len(new) < 10:
            flash("New password must be at least 10 characters.")
        else:
            db().execute("UPDATE members SET pw_hash=?, must_change=0 WHERE id=?",
                         (generate_password_hash(new), g.user["id"]))
            log("changed own password")
            db().commit()
            flash("Password updated.")
            return redirect(url_for("home"))
    return render_template("password.html")

# ---------- permissions & the approval engine ----------
def now():
    return datetime.now().isoformat(timespec="seconds")

def is_top(u=None):
    u = u or g.get("user")
    return bool(u) and u["role"] in TOP

def can(perm, u=None):
    u = u or g.get("user")
    return bool(u) and (u["role"] in TOP or u["role"] in PERMS.get(perm, ()))

def cleared(pid):
    return bool(g.get("user")) and bool(db().execute(
        "SELECT 1 FROM project_editors WHERE project_id=? AND member_id=?", (pid, g.user["id"])).fetchone())

def can_edit_project(pid):
    return can("projects_manage") or cleared(pid)

def pending_count():
    u = g.get("user")
    if not u or u["role"] not in TOP:
        return 0
    return db().execute("SELECT COUNT(*) FROM changes c WHERE status='pending' AND NOT EXISTS "
                        "(SELECT 1 FROM approvals a WHERE a.change_id=c.id AND a.approver_id=?)", (u["id"],)).fetchone()[0]

MEMBER_COLS = {"name", "email", "phone", "department", "level", "position", "student_id", "date_joined",
               "emergency_name", "emergency_phone", "role", "active", "pw_hash", "must_change"}
MEETING_COLS = {"title", "held_on", "location"}
PROJECT_COLS = {"name", "status", "summary", "start_date", "end_date", "budget", "impact", "lead_id"}
PAY_COLS = {"kind", "description", "amount", "paid_on", "method", "note"}

def _upd(d, table, rid, data, allowed):
    cols = [k for k in data if k in allowed]
    if cols:
        d.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in cols)} WHERE id=?", [data[c] for c in cols] + [rid])

def _ins(d, table, data, allowed):
    cols = [k for k in data if k in allowed]
    return d.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                     [data[c] for c in cols]).lastrowid

def a_attendance(d, p):
    for mid, st in p["status"].items():
        d.execute("INSERT OR REPLACE INTO attendance(meeting_id,member_id,present,status) VALUES(?,?,?,?)",
                  (p["meeting_id"], int(mid), 1 if st in ("present", "late") else 0, st))
    d.execute("UPDATE meetings SET notes=? WHERE id=?", (p.get("notes", ""), p["meeting_id"]))

def a_member(d, p):
    if p.get("id"):
        _upd(d, "members", p["id"], p["data"], MEMBER_COLS)
        return p["id"]
    return _ins(d, "members", p["data"], MEMBER_COLS)

def a_meeting(d, p):
    if p.get("id"):
        _upd(d, "meetings", p["id"], p["data"], MEETING_COLS)
        return p["id"]
    return _ins(d, "meetings", p["data"], MEETING_COLS)

def a_ann_new(d, p):
    return d.execute("INSERT INTO announcements(title,body,author,at) VALUES(?,?,?,?)",
                     (p["title"], p["body"], p["author"], now())).lastrowid

def a_ann_del(d, p):
    d.execute("UPDATE announcements SET active=0 WHERE id=?", (p["id"],))

def a_project(d, p):
    pid = p.get("id")
    if pid:
        _upd(d, "projects", pid, p["data"], PROJECT_COLS)
    else:
        pid = _ins(d, "projects", p["data"], PROJECT_COLS)
    if "team" in p:
        d.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
        for m in p["team"]:
            d.execute("INSERT OR IGNORE INTO project_members VALUES(?,?)", (pid, m))
    return pid

def a_file_add(d, p):
    d.execute("INSERT INTO project_files(project_id,name,stored,size,by,at) VALUES(?,?,?,?,?,?)",
              (p["project_id"], p["name"], p["stored"], p["size"], p["by"], now()))

def a_file_del(d, p):
    r = d.execute("SELECT * FROM project_files WHERE id=?", (p["id"],)).fetchone()
    if r:
        d.execute("DELETE FROM project_files WHERE id=?", (p["id"],))
        try:
            os.remove(os.path.join(UPLOAD_DIR, r["stored"]))
        except OSError:
            pass

def a_pay_edit(d, p):
    _upd(d, "payments", p["id"], p["data"], PAY_COLS)

def a_pay_void(d, p):
    d.execute("UPDATE payments SET voided=1, void_reason=? WHERE id=?", (p["reason"], p["id"]))

APPLY = {"attendance": a_attendance, "member": a_member, "meeting": a_meeting, "ann_new": a_ann_new,
         "ann_del": a_ann_del, "project": a_project, "file_add": a_file_add, "file_del": a_file_del,
         "pay_edit": a_pay_edit, "pay_void": a_pay_void}

def submit_change(kind, payload, summary, details="", ref=None):
    """President/VP changes apply at once. Everyone else's are queued for approval.
    Returns (applied, id)."""
    d = db()
    if is_top():
        rid = APPLY[kind](d, payload)
        log(summary)
        d.commit()
        return True, rid
    if ref:
        d.execute("UPDATE changes SET status='superseded' WHERE ref=? AND kind=? AND status='pending' AND by_id=?",
                  (ref, kind, g.user["id"]))
    cur = d.execute("INSERT INTO changes(kind,ref,payload,summary,details,by_id,at) VALUES(?,?,?,?,?,?,?)",
                    (kind, ref, json.dumps(payload), summary, details, g.user["id"], now()))
    log(f"submitted for approval: {summary}")
    d.commit()
    return False, cur.lastrowid

def told(applied):
    who = "the President and Vice President" if APPROVAL_MODE == "both" else "the President or Vice President"
    flash("Saved." if applied else f"Sent for approval. It takes effect once {who} approve it.")

def diff_lines(old, new, labels):
    out = []
    for k, label in labels.items():
        if k in new:
            a = "" if old is None or old[k] is None else str(old[k])
            b = "" if new[k] is None else str(new[k])
            if a != b:
                out.append(f"{label}: {a or '(empty)'} → {b or '(empty)'}")
    return out

def _isdate(v):
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False

@app.route("/approvals")
@need()
def approvals():
    d = db()
    def pack(rows):
        out = []
        for c in rows:
            votes = d.execute("SELECT a.*, m.name FROM approvals a JOIN members m ON m.id=a.approver_id "
                              "WHERE a.change_id=? ORDER BY a.at", (c["id"],)).fetchall()
            out.append(dict(c=c, votes=votes, mine=any(v["approver_id"] == g.user["id"] for v in votes)))
        return out
    q = ("SELECT c.*, m.name by_name, m.role by_role FROM changes c JOIN members m ON m.id=c.by_id ")
    if is_top():
        pend = pack(d.execute(q + "WHERE c.status='pending' ORDER BY c.id").fetchall())
        hist = pack(d.execute(q + "WHERE c.status!='pending' ORDER BY c.id DESC LIMIT 40").fetchall())
    else:
        pend = pack(d.execute(q + "WHERE c.status='pending' AND c.by_id=? ORDER BY c.id", (g.user["id"],)).fetchall())
        hist = pack(d.execute(q + "WHERE c.status!='pending' AND c.by_id=? ORDER BY c.id DESC LIMIT 40", (g.user["id"],)).fetchall())
    return render_template("approvals.html", pend=pend, hist=hist)

@app.route("/approvals/<int:cid>/decide", methods=["POST"])
@need(top=True)
def decide(cid):
    d = db()
    c = d.execute("SELECT * FROM changes WHERE id=? AND status='pending'", (cid,)).fetchone() or abort(404)
    verdict, note = request.form.get("decision"), request.form.get("note", "").strip()
    if verdict not in ("approve", "reject"):
        abort(400)
    d.execute("INSERT OR REPLACE INTO approvals VALUES(?,?,?,?,?,?)",
              (cid, g.user["id"], g.user["role"], verdict, note, now()))
    payload = json.loads(c["payload"])
    if verdict == "reject":
        d.execute("UPDATE changes SET status='rejected', decided_at=?, note=? WHERE id=?", (now(), note, cid))
        if c["kind"] == "file_add":
            try:
                os.remove(os.path.join(UPLOAD_DIR, payload["stored"]))
            except OSError:
                pass
        log(f"rejected: {c['summary']}")
        flash("Change rejected.")
    else:
        got = {r[0] for r in d.execute("SELECT role FROM approvals WHERE change_id=? AND decision='approve'", (cid,))}
        have = {r[0] for r in d.execute("SELECT role FROM members WHERE active=1 AND role IN ('president','vice_president')")}
        ok = bool(got & have) if APPROVAL_MODE == "either" else have <= got
        if ok:
            try:
                APPLY[c["kind"]](d, payload)
            except sqlite3.IntegrityError:
                d.rollback()
                d.execute("UPDATE changes SET status='failed', decided_at=?, note=? WHERE id=?",
                          (now(), "Clashed with existing data (for example a duplicate email).", cid))
                d.commit()
                flash("That change could not be applied because it clashes with existing data.")
                return redirect(url_for("approvals"))
            d.execute("UPDATE changes SET status='approved', decided_at=? WHERE id=?", (now(), cid))
            log(f"approved and applied: {c['summary']}")
            flash("Approved and applied.")
        else:
            log(f"approved (waiting for the other approver): {c['summary']}")
            flash("Your approval is recorded. Waiting for the other approver.")
    d.commit()
    return redirect(url_for("approvals"))

# ---------- dashboard ----------
@app.route("/")
@need()
def home():
    d = db()
    stats = dict(
        members=d.execute("SELECT COUNT(*) FROM members WHERE active=1").fetchone()[0],
        meetings=d.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
        ongoing=d.execute("SELECT COUNT(*) FROM projects WHERE status='Ongoing'").fetchone()[0],
        done=d.execute("SELECT COUNT(*) FROM projects WHERE status='Completed'").fetchone()[0])
    mine = d.execute("SELECT COALESCE(SUM(status IN ('present','late')),0) a, COALESCE(SUM(status!='excused'),0) t "
                     "FROM attendance WHERE member_id=?", (g.user["id"],)).fetchone()
    projects = d.execute("SELECT id,name,status FROM projects WHERE status IN ('Ongoing','Planning') ORDER BY name").fetchall()
    recent = d.execute("SELECT m.id,m.title,m.held_on,"
                       "(SELECT COALESCE(SUM(status IN ('present','late')),0) FROM attendance WHERE meeting_id=m.id) p,"
                       "(SELECT COALESCE(SUM(status!='excused'),0) FROM attendance WHERE meeting_id=m.id) t "
                       "FROM meetings m ORDER BY held_on DESC LIMIT 5").fetchall() if can("attendance_view") else []
    anns = d.execute("SELECT * FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 3").fetchall()
    subs = d.execute("SELECT summary,status,at FROM changes WHERE by_id=? ORDER BY id DESC LIMIT 5", (g.user["id"],)).fetchall()
    my_units = [r[0] for r in d.execute("SELECT u.name FROM member_units mu JOIN units u ON u.id=mu.unit_id WHERE mu.member_id=? ORDER BY u.name", (g.user["id"],))]
    return render_template("home.html", stats=stats, mine=mine, projects=projects, recent=recent, anns=anns, subs=subs, my_units=my_units)

# ---------- announcements ----------
@app.route("/announcements")
@need()
def announcements():
    rows = db().execute("SELECT * FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("announcements.html", rows=rows)

@app.route("/announcements/new", methods=["GET", "POST"])
@need("announce")
def announcement_new():
    if request.method == "POST":
        t, b = request.form.get("title", "").strip(), request.form.get("body", "").strip()
        if not t:
            flash("Give the announcement a title.")
        else:
            ok, _ = submit_change("ann_new", {"title": t, "body": b, "author": g.user["name"]},
                                  f"Post announcement: {t}", b[:300])
            told(ok)
            return redirect(url_for("announcements"))
    return render_template("announcement_form.html")

@app.route("/announcements/<int:aid>/delete", methods=["POST"])
@need("announce")
def announcement_delete(aid):
    a = db().execute("SELECT * FROM announcements WHERE id=? AND active=1", (aid,)).fetchone() or abort(404)
    ok, _ = submit_change("ann_del", {"id": aid}, f"Remove announcement: {a['title']}")
    told(ok)
    return redirect(url_for("announcements"))

# ---------- members ----------
LABELS_M = {"name": "Name", "email": "Email", "phone": "Phone", "department": "Department", "level": "Level",
            "position": "Position", "student_id": "Student ID", "date_joined": "Date joined",
            "emergency_name": "Emergency contact", "emergency_phone": "Emergency phone", "role": "Portfolio",
            "active": "Active"}

@app.route("/members")
@need("members_view")
def members():
    rows = db().execute("SELECT m.*, (SELECT GROUP_CONCAT(u.name, ', ') FROM member_units mu JOIN units u ON u.id=mu.unit_id WHERE mu.member_id=m.id) units, "
                        "COALESCE(SUM(a.status IN ('present','late')),0) attended, COALESCE(SUM(a.status!='excused'),0) recorded "
                        "FROM members m LEFT JOIN attendance a ON a.member_id=m.id "
                        "GROUP BY m.id ORDER BY m.active DESC, m.name").fetchall()
    return render_template("members.html", rows=rows)

@app.route("/members/new", methods=["GET", "POST"])
@app.route("/members/<int:mid>/edit", methods=["GET", "POST"])
@need("members_edit")
def member_form(mid=None):
    d = db()
    m = d.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone() if mid else None
    if mid and not m:
        abort(404)
    top = is_top()
    if request.method == "POST":
        f = request.form
        data = {k: f.get(k, "").strip() for k in ("name", "email", "phone", "department", "level", "position",
                                                    "student_id", "date_joined", "emergency_name", "emergency_phone")}
        data["email"] = data["email"].lower()
        pw = f.get("password", "")
        err = None
        if top:
            data["role"] = f.get("role", m["role"] if m else "member")
            data["active"] = (1 if f.get("active") else 0) if m else 1
            if data["role"] not in ROLES:
                abort(400)
            if m and m["id"] == g.user["id"] and (data["role"] != m["role"] or not data["active"]):
                err = "You can't change your own portfolio or deactivate yourself."
            if not data["position"]:
                data["position"] = ROLES[data["role"]]
            if pw:
                data["pw_hash"], data["must_change"] = generate_password_hash(pw), 1
            elif not m:
                data["must_change"] = 0
        elif not m:
            data["role"], data["must_change"] = "member", 0
        if err:
            pass
        elif not data["name"] or not data["email"]:
            err = "Name and email are required."
        elif pw and len(pw) < 10:
            err = "Password must be at least 10 characters."
        elif data["date_joined"] and not _isdate(data["date_joined"]):
            err = "Enter a valid date joined."
        elif d.execute("SELECT 1 FROM members WHERE email=? AND id IS NOT ?", (data["email"], mid)).fetchone():
            err = "That email is already registered."
        if err:
            flash(err)
        else:
            details = diff_lines(m, data, LABELS_M) if m else [f"{LABELS_M.get(k, k)}: {v}" for k, v in data.items()
                                                              if v not in ("", None) and k in LABELS_M]
            if m and not details and not pw and not (top and request.form.get("units_present")):
                flash("No changes made.")
                return redirect(url_for("members"))
            if pw:
                details.append("Password: reset")
            ok, rid = submit_change("member", {"id": mid, "data": data},
                                    f"{'Edit' if m else 'Add'} member {data['name']}", "\n".join(details),
                                    ref=f"member:{mid}" if mid else None)
            if ok and top and f.get("units_present"):
                set_member_units(rid, _ids(f.getlist("unit")))
                db().commit()
            told(ok)
            return redirect(url_for("members"))
    link = session.pop("reset_link", None) if mid else None
    all_units = d.execute("SELECT id,name FROM units ORDER BY name").fetchall()
    mine = {r[0] for r in d.execute("SELECT unit_id FROM member_units WHERE member_id=?", (mid,))} if mid else set()
    return render_template("member_form.html", m=m, admin=top, link=link, all_units=all_units, my_units=mine)

# ---------- units (IT, Field Agents, Research...) ----------
def set_member_units(mid, unit_ids):
    d = db()
    valid = {r[0] for r in d.execute("SELECT id FROM units")}
    d.execute("DELETE FROM member_units WHERE member_id=?", (mid,))
    for u in set(unit_ids):
        if u in valid:
            d.execute("INSERT INTO member_units VALUES(?,?)", (mid, u))

def _ids(vals):
    return [int(v) for v in vals if str(v).isdigit()]

@app.route("/units", methods=["GET", "POST"])
@need("members_view")
def units():
    d = db()
    if request.method == "POST":
        if not is_top():
            abort(403)
        name, desc = request.form.get("name", "").strip()[:40], request.form.get("description", "").strip()[:200]
        if not name:
            flash("Give the unit a name.")
        else:
            try:
                d.execute("INSERT INTO units(name,description) VALUES(?,?)", (name, desc))
                log(f"created unit {name}")
                d.commit()
                flash(f"Unit {name} created. Open it to add people.")
            except sqlite3.IntegrityError:
                flash("A unit with that name already exists.")
        return redirect(url_for("units"))
    rows = d.execute("SELECT u.id,u.name,u.description,COUNT(m.id) n FROM units u "
                     "LEFT JOIN member_units mu ON mu.unit_id=u.id LEFT JOIN members m ON m.id=mu.member_id AND m.active=1 "
                     "GROUP BY u.id ORDER BY u.name").fetchall()
    none = d.execute("SELECT COUNT(*) FROM members WHERE active=1 AND id NOT IN (SELECT member_id FROM member_units)").fetchone()[0]
    return render_template("units.html", rows=rows, none=none)

@app.route("/units/<int:uid>")
@need("members_view")
def unit(uid):
    d = db()
    u = d.execute("SELECT * FROM units WHERE id=?", (uid,)).fetchone() or abort(404)
    people = d.execute("SELECT m.id,m.name,m.position,m.role,m.active,"
                       "EXISTS(SELECT 1 FROM member_units WHERE unit_id=? AND member_id=m.id) inu FROM members m "
                       "WHERE m.active=1 OR m.id IN (SELECT member_id FROM member_units WHERE unit_id=?) ORDER BY m.name", (uid, uid)).fetchall()
    return render_template("unit.html", u=u, people=people)

@app.route("/units/<int:uid>/members", methods=["POST"])
@need(top=True)
def unit_members(uid):
    d = db()
    u = d.execute("SELECT name FROM units WHERE id=?", (uid,)).fetchone() or abort(404)
    chosen = set(_ids(request.form.getlist("member")))
    d.execute("DELETE FROM member_units WHERE unit_id=?", (uid,))
    for m in d.execute("SELECT id FROM members").fetchall():
        if m["id"] in chosen:
            d.execute("INSERT INTO member_units VALUES(?,?)", (m["id"], uid))
    log(f"set members of unit {u['name']} ({len(chosen)} people)")
    d.commit()
    flash(f"Unit {u['name']} now has {len(chosen)} member{'s' if len(chosen) != 1 else ''}.")
    return redirect(url_for("unit", uid=uid))

@app.route("/units/<int:uid>/rename", methods=["POST"])
@need(top=True)
def unit_rename(uid):
    d = db()
    d.execute("SELECT 1 FROM units WHERE id=?", (uid,)).fetchone() or abort(404)
    name, desc = request.form.get("name", "").strip()[:40], request.form.get("description", "").strip()[:200]
    if not name:
        flash("The unit needs a name.")
    else:
        try:
            d.execute("UPDATE units SET name=?, description=? WHERE id=?", (name, desc, uid))
            log(f"renamed unit {uid} to {name}")
            d.commit()
            flash("Unit updated.")
        except sqlite3.IntegrityError:
            flash("A unit with that name already exists.")
    return redirect(url_for("unit", uid=uid))

@app.route("/units/<int:uid>/delete", methods=["POST"])
@need(top=True)
def unit_delete(uid):
    d = db()
    u = d.execute("SELECT name FROM units WHERE id=?", (uid,)).fetchone() or abort(404)
    d.execute("DELETE FROM units WHERE id=?", (uid,))
    log(f"deleted unit {u['name']}")
    d.commit()
    flash(f"Unit {u['name']} deleted. Its members are not affected otherwise.")
    return redirect(url_for("units"))

# ---------- meetings & attendance ----------
@app.route("/meetings")
@need()
def meetings():
    rows = db().execute("SELECT m.*, (SELECT COALESCE(SUM(status IN ('present','late')),0) FROM attendance WHERE meeting_id=m.id) p,"
                        "(SELECT COALESCE(SUM(status!='excused'),0) FROM attendance WHERE meeting_id=m.id) t,"
                        "(SELECT status FROM attendance WHERE meeting_id=m.id AND member_id=?) mine "
                        "FROM meetings m ORDER BY held_on DESC", (g.user["id"],)).fetchall()
    return render_template("meetings.html", rows=rows)

@app.route("/meetings/new", methods=["GET", "POST"])
@app.route("/meetings/<int:mid>/edit", methods=["GET", "POST"])
@need("meetings_manage")
def meeting_form(mid=None):
    m = db().execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone() if mid else None
    if mid and not m:
        abort(404)
    if request.method == "POST":
        data = {k: request.form.get(k, "").strip() for k in ("title", "held_on", "location")}
        if not data["title"] or not _isdate(data["held_on"]):
            flash("Enter a title and a valid date.")
        else:
            details = "\n".join(diff_lines(m, data, {"title": "Title", "held_on": "Date", "location": "Location"})) if m else \
                f"Date: {data['held_on']}\nLocation: {data['location'] or '(none)'}"
            if m and not details:
                flash("No changes made.")
                return redirect(url_for("meetings"))
            ok, rid = submit_change("meeting", {"id": mid, "data": data},
                                    f"{'Edit' if m else 'Schedule'} meeting {data['title']}", details,
                                    ref=f"meeting-info:{mid}" if mid else None)
            told(ok)
            return redirect(url_for("meetings"))
    return render_template("meeting_form.html", m=m, today=date.today().isoformat())

@app.route("/meetings/<int:mid>", methods=["GET", "POST"])
@need("attendance_view")
def meeting(mid):
    d = db()
    m = d.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone() or abort(404)
    if request.method == "POST":
        if not can("attendance_edit"):
            abort(403)
        status = {}
        for r in d.execute("SELECT id FROM members WHERE active=1").fetchall():
            st = request.form.get(f"s_{r['id']}")
            status[str(r["id"])] = st if st in ATT else "absent"
        tally = {s: list(status.values()).count(s) for s in ATT}
        summary = f"Attendance for {m['title']} ({m['held_on']})"
        ok, _ = submit_change("attendance", {"meeting_id": mid, "status": status, "notes": request.form.get("notes", "").strip()},
                              summary, ", ".join(f"{n} {s}" for s, n in tally.items()), ref=f"attendance:{mid}")
        told(ok)
        return redirect(url_for("meeting", mid=mid))
    people = d.execute("SELECT m.id,m.name,m.position,a.status,"
                       "(SELECT ',' || GROUP_CONCAT(unit_id) || ',' FROM member_units WHERE member_id=m.id) uids FROM members m "
                       "LEFT JOIN attendance a ON a.member_id=m.id AND a.meeting_id=? "
                       "WHERE m.active=1 OR a.meeting_id IS NOT NULL ORDER BY m.name", (mid,)).fetchall()
    pend = d.execute("SELECT c.at, u.name FROM changes c JOIN members u ON u.id=c.by_id "
                     "WHERE c.kind='attendance' AND c.ref=? AND c.status='pending' ORDER BY c.id DESC LIMIT 1",
                     (f"attendance:{mid}",)).fetchone()
    ulist = d.execute("SELECT id,name FROM units ORDER BY name").fetchall()
    return render_template("meeting.html", m=m, people=people, pend=pend, editable=can("attendance_edit"), ulist=ulist)

# ---------- projects ----------
LABELS_P = {"name": "Name", "status": "Status", "summary": "Summary", "start_date": "Start date",
            "end_date": "End date", "budget": "Budget", "impact": "Impact", "lead_id": "Lead (member id)"}

@app.route("/projects")
@need()
def projects():
    rows = db().execute("SELECT p.*, m.name lead FROM projects p LEFT JOIN members m ON m.id=p.lead_id "
                        "ORDER BY CASE p.status WHEN 'Ongoing' THEN 0 WHEN 'Planning' THEN 1 "
                        "WHEN 'Paused' THEN 2 ELSE 3 END, p.name").fetchall()
    return render_template("projects.html", rows=rows)

@app.route("/projects/<int:pid>")
@need()
def project(pid):
    d = db()
    p = d.execute("SELECT p.*, m.name lead FROM projects p LEFT JOIN members m ON m.id=p.lead_id "
                  "WHERE p.id=?", (pid,)).fetchone() or abort(404)
    team = d.execute("SELECT m.name, m.position FROM project_members pm JOIN members m ON m.id=pm.member_id "
                     "WHERE pm.project_id=? ORDER BY m.name", (pid,)).fetchall()
    files = d.execute("SELECT * FROM project_files WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()
    editors = d.execute("SELECT m.id, m.name FROM project_editors e JOIN members m ON m.id=e.member_id "
                        "WHERE e.project_id=? ORDER BY m.name", (pid,)).fetchall()
    others = d.execute("SELECT id, name FROM members WHERE active=1 AND id NOT IN "
                       "(SELECT member_id FROM project_editors WHERE project_id=?) ORDER BY name", (pid,)).fetchall() \
        if can("projects_manage") else []
    ed = can_edit_project(pid)
    waiting = d.execute("SELECT COUNT(*) FROM changes WHERE status='pending' AND (ref=? OR (kind IN ('file_add','file_del') AND "
                        "payload LIKE ?))", (f"project:{pid}", f'%"project_id": {pid},%')).fetchone()[0] if ed else 0
    return render_template("project.html", p=p, team=team, files=files, editors=editors, others=others,
                           can_edit=ed, waiting=waiting)

@app.route("/projects/new", methods=["GET", "POST"])
@app.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
@need()
def project_form(pid=None):
    d = db()
    p = d.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone() if pid else None
    if pid and not p:
        abort(404)
    mgr = can("projects_manage")
    if (pid is None and not mgr) or (pid and not can_edit_project(pid)):
        abort(403)
    if request.method == "POST":
        f = request.form
        data = {"status": f.get("status", "Planning"), "summary": f.get("summary", "").strip(),
                "impact": f.get("impact", "").strip(), "start_date": f.get("start_date") or None,
                "end_date": f.get("end_date") or None}
        err = None
        if mgr:
            data["name"] = f.get("name", "").strip()
            data["lead_id"] = int(f["lead_id"]) if f.get("lead_id", "").isdigit() else None
            try:
                data["budget"] = float(f.get("budget") or 0)
            except ValueError:
                err = "Enter the budget as a number."
            if not data["name"]:
                err = "Project name is required."
        for k in ("start_date", "end_date"):
            if data[k] and not _isdate(data[k]):
                err = "Check the dates."
        if data["status"] not in STATUSES:
            abort(400)
        if err:
            flash(err)
        else:
            payload = {"id": pid, "data": data}
            details = diff_lines(p, data, LABELS_P) if p else [f"{LABELS_P[k]}: {v}" for k, v in data.items() if v not in (None, "")]
            if mgr:
                team = sorted({int(x) for x in f.getlist("team") if x.isdigit()})
                payload["team"] = team
                old = {r[0] for r in d.execute("SELECT member_id FROM project_members WHERE project_id=?", (pid,))} if pid else set()
                names = {r["id"]: r["name"] for r in d.execute("SELECT id,name FROM members")}
                for label, ids in (("Team added", set(team) - old), ("Team removed", old - set(team))):
                    if ids:
                        details.append(f"{label}: {', '.join(names.get(i, '?') for i in sorted(ids))}")
            if p and not details:
                flash("No changes made.")
                return redirect(url_for("project", pid=pid))
            title = data.get("name") or p["name"]
            ok, rid = submit_change("project", payload, f"{'Edit' if p else 'Create'} project {title}",
                                    "\n".join(details), ref=f"project:{pid}" if pid else None)
            told(ok)
            return redirect(url_for("project", pid=pid or rid) if (pid or ok) else url_for("projects"))
    people = d.execute("SELECT id,name FROM members WHERE active=1 ORDER BY name").fetchall()
    team = {r[0] for r in d.execute("SELECT member_id FROM project_members WHERE project_id=?", (pid,))} if pid else set()
    return render_template("project_form.html", p=p, people=people, team=team, statuses=STATUSES, mgr=mgr)

@app.route("/projects/<int:pid>/editors", methods=["POST"])
@need("projects_manage")
def add_editor(pid):
    mid = request.form.get("member_id", "")
    m = db().execute("SELECT name FROM members WHERE id=? AND active=1", (mid,)).fetchone() if mid.isdigit() else None
    p = db().execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone() or abort(404)
    if m:
        db().execute("INSERT OR IGNORE INTO project_editors VALUES(?,?)", (pid, int(mid)))
        log(f"gave {m['name']} edit clearance on project {p['name']}")
        db().commit()
        flash(f"{m['name']} can now edit this project. Their edits still need approval.")
    return redirect(url_for("project", pid=pid))

@app.route("/projects/<int:pid>/editors/<int:mid>/remove", methods=["POST"])
@need("projects_manage")
def remove_editor(pid, mid):
    db().execute("DELETE FROM project_editors WHERE project_id=? AND member_id=?", (pid, mid))
    log(f"removed edit clearance (member {mid}) on project {pid}")
    db().commit()
    flash("Clearance removed.")
    return redirect(url_for("project", pid=pid))

@app.route("/projects/<int:pid>/files", methods=["POST"])
@need()
def upload_file(pid):
    db().execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() or abort(404)
    if not can_edit_project(pid):
        abort(403)
    f = request.files.get("file")
    ext = (f.filename.rsplit(".", 1)[-1].lower() if f and "." in f.filename else "")
    if not f or not f.filename:
        flash("Choose a file to upload.")
    elif ext not in ALLOWED_EXT:
        flash("That file type is not allowed. Use PDF, Word, Excel, PowerPoint, image, TXT or CSV.")
    else:
        stored = secrets.token_hex(16) + "." + ext
        path = os.path.join(UPLOAD_DIR, stored)
        f.save(path)
        name = os.path.basename(f.filename)[:120]
        ok, _ = submit_change("file_add", {"project_id": pid, "name": name, "stored": stored,
                                           "size": os.path.getsize(path), "by": g.user["name"]},
                              f"Add document {name} to project {pid}")
        told(ok)
    return redirect(url_for("project", pid=pid))

@app.route("/files/<int:fid>")
@need()
def get_file(fid):
    r = db().execute("SELECT * FROM project_files WHERE id=?", (fid,)).fetchone() or abort(404)
    return send_from_directory(UPLOAD_DIR, r["stored"], as_attachment=True, download_name=r["name"])

@app.route("/files/<int:fid>/delete", methods=["POST"])
@need()
def delete_file(fid):
    r = db().execute("SELECT * FROM project_files WHERE id=?", (fid,)).fetchone() or abort(404)
    if not can_edit_project(r["project_id"]):
        abort(403)
    ok, _ = submit_change("file_del", {"id": fid}, f"Delete document {r['name']}")
    told(ok)
    return redirect(url_for("project", pid=r["project_id"]))

@app.errorhandler(413)
def too_big(_):
    flash("That file is too large. The limit is 12 MB.")
    return redirect(request.referrer or url_for("home"))

# ---------- finance: dues, payments, emailed receipts ----------
def money(v):
    return f"{CURRENCY} {v:,.2f}"

def smtp_ready():
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))

def smtp_send(msg):
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=15)
        s.starttls(context=ctx)
    s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
    s.send_message(msg)
    s.quit()

def send_receipt(p, m):
    """Email a receipt. Returns (ok, message)."""
    if not m["email"]:
        return False, "This member has no email address."
    if not smtp_ready():
        return False, "Email is not set up yet, so the receipt was saved but not sent (see the README)."
    e = html.escape
    lines = [("Receipt no.", p["receipt_no"]), ("Received from", m["name"]), ("For", f"{p['kind']}" + (f" - {p['description']}" if p["description"] else "")),
             ("Amount", money(p["amount"])), ("Date paid", p["paid_on"]), ("Method", p["method"] or "-"),
             ("Processed by", p["recorded_by"] or "-")]
    if p["note"]:
        lines.append(("Note", p["note"]))
    msg = EmailMessage()
    msg["Subject"] = f"Receipt {p['receipt_no']} from {ORG}"
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["To"] = m["email"]
    msg.set_content(f"Hello {m['name']},\n\nThank you. This confirms your payment to {ORG}.\n\n" +
                    "\n".join(f"{k}: {v}" for k, v in lines) + f"\n\n{ORG} Finance\n(System by {BUILT_BY})")
    rows = "".join(f"<tr><td style='padding:6px 14px 6px 0;color:#5d6778'>{e(k)}</td><td style='padding:6px 0'><b>{e(str(v))}</b></td></tr>" for k, v in lines)
    msg.add_alternative(f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:520px'><h2 style='border-bottom:3px solid #f2b705;padding-bottom:8px'>{e(ORG)} - Payment receipt</h2>"
                        f"<p>Hello {e(m['name'])}, thank you. This confirms your payment.</p><table>{rows}</table>"
                        f"<p style='color:#5d6778;font-size:13px'>Keep this email as your proof of payment.</p><p style='color:#8a93a3;font-size:12px'>{e(ORG)} MIS - System by {e(BUILT_BY)}</p></div>", subtype="html")
    try:
        smtp_send(msg)
    except Exception as ex:
        return False, f"The receipt was saved but the email could not be sent ({ex.__class__.__name__}). Use Resend email to try again."
    return True, f"Receipt emailed to {m['email']}."

def email_and_record(pid):
    d = db()
    p = d.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    m = d.execute("SELECT * FROM members WHERE id=?", (p["member_id"],)).fetchone()
    ok, msg = send_receipt(p, m)
    d.execute("UPDATE payments SET emailed_at=?, email_status=? WHERE id=?", (now() if ok else None, "sent" if ok else msg, pid))
    log(f"{'emailed' if ok else 'could not email'} receipt {p['receipt_no']}")
    d.commit()
    return msg

@app.route("/finance")
@need("finance")
def finance():
    d = db()
    rows = d.execute("SELECT p.*, m.name member FROM payments p JOIN members m ON m.id=p.member_id "
                     "ORDER BY p.id DESC LIMIT 300").fetchall()
    tot = d.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE voided=0").fetchone()[0]
    dues = d.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE voided=0 AND kind='Dues'").fetchone()[0]
    per = d.execute("SELECT m.name, COALESCE(SUM(p.amount),0) paid FROM members m LEFT JOIN payments p "
                    "ON p.member_id=m.id AND p.voided=0 AND p.kind='Dues' WHERE m.active=1 GROUP BY m.id ORDER BY paid, m.name").fetchall()
    return render_template("finance.html", rows=rows, tot=tot, dues=dues, per=per, money=money, mail=smtp_ready())

@app.route("/finance/new", methods=["GET", "POST"])
@need("finance")
def payment_new():
    d = db()
    people = d.execute("SELECT id,name,email FROM members WHERE active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        f = request.form
        mem = d.execute("SELECT * FROM members WHERE id=? AND active=1", (f.get("member_id", ""),)).fetchone()
        try:
            amount = round(float(f.get("amount", "")), 2)
        except ValueError:
            amount = 0
        kind = f.get("kind", "Dues")
        if not mem or amount <= 0 or not _isdate(f.get("paid_on", "")) or kind not in PAY_KINDS or amount > 10_000_000:
            flash("Choose a member, enter an amount above zero, and give a valid date.")
        else:
            pid = d.execute("INSERT INTO payments(member_id,kind,description,amount,paid_on,method,note,recorded_by,created_at,receipt_no) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (mem["id"], kind, f.get("description", "").strip(), amount, f["paid_on"],
                             f.get("method") if f.get("method") in PAY_METHODS else "Other", f.get("note", "").strip(),
                             g.user["name"], now(), f"TMP-{secrets.token_hex(4)}")).lastrowid
            d.execute("UPDATE payments SET receipt_no=? WHERE id=?", (f"ENA-{f['paid_on'][:4]}-{pid:04d}", pid))
            log(f"recorded payment {money(amount)} from {mem['name']} ({kind})")
            d.commit()
            flash(email_and_record(pid) if f.get("send") else "Payment recorded. No email was sent.")
            return redirect(url_for("receipt", pid=pid))
    return render_template("payment_form.html", p=None, people=people, kinds=PAY_KINDS, methods=PAY_METHODS, today=date.today().isoformat())

@app.route("/finance/<int:pid>")
@need("finance")
def receipt(pid):
    p = db().execute("SELECT p.*, m.name member, m.email email FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (pid,)).fetchone() or abort(404)
    return render_template("receipt.html", p=p, money=money, mail=smtp_ready())

@app.route("/finance/<int:pid>/resend", methods=["POST"])
@need("finance")
def resend_receipt(pid):
    db().execute("SELECT 1 FROM payments WHERE id=?", (pid,)).fetchone() or abort(404)
    flash(email_and_record(pid))
    return redirect(url_for("receipt", pid=pid))

@app.route("/finance/<int:pid>/edit", methods=["GET", "POST"])
@need("finance")
def payment_edit(pid):
    p = db().execute("SELECT p.*, m.name member FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (pid,)).fetchone() or abort(404)
    if p["voided"]:
        flash("A voided payment can't be edited.")
        return redirect(url_for("receipt", pid=pid))
    if request.method == "POST":
        f = request.form
        try:
            amount = round(float(f.get("amount", "")), 2)
        except ValueError:
            amount = 0
        data = {"kind": f.get("kind"), "description": f.get("description", "").strip(), "amount": amount,
                "paid_on": f.get("paid_on", ""), "method": f.get("method"), "note": f.get("note", "").strip()}
        if amount <= 0 or not _isdate(data["paid_on"]) or data["kind"] not in PAY_KINDS or data["method"] not in PAY_METHODS:
            flash("Check the amount, date, type and method.")
        else:
            details = diff_lines(p, data, {"kind": "Type", "description": "Description", "amount": "Amount", "paid_on": "Date paid", "method": "Method", "note": "Note"})
            if not details:
                flash("No changes made.")
                return redirect(url_for("receipt", pid=pid))
            ok, _ = submit_change("pay_edit", {"id": pid, "data": data}, f"Edit payment {p['receipt_no']} ({p['member']})",
                                  "\n".join(details), ref=f"payment:{pid}")
            told(ok)
            return redirect(url_for("receipt", pid=pid))
    return render_template("payment_form.html", p=p, people=[], kinds=PAY_KINDS, methods=PAY_METHODS, today=date.today().isoformat())

@app.route("/finance/<int:pid>/void", methods=["POST"])
@need("finance")
def payment_void(pid):
    p = db().execute("SELECT p.*, m.name member FROM payments p JOIN members m ON m.id=p.member_id WHERE p.id=?", (pid,)).fetchone() or abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason for voiding this payment.")
    else:
        ok, _ = submit_change("pay_void", {"id": pid, "reason": reason}, f"Void payment {p['receipt_no']} ({p['member']}, {money(p['amount'])})",
                              f"Reason: {reason}", ref=f"payment:{pid}")
        told(ok)
    return redirect(url_for("receipt", pid=pid))

# ---------- reports, exports, audit ----------
@app.route("/reports")
@need("reports")
def reports():
    d = db()
    trend = d.execute("SELECT m.held_on, m.title, COALESCE(SUM(a.status IN ('present','late')),0) p, "
                      "COALESCE(SUM(a.status!='excused'),0) t FROM meetings m LEFT JOIN attendance a ON a.meeting_id=m.id "
                      "GROUP BY m.id ORDER BY m.held_on DESC LIMIT 12").fetchall()[::-1]
    people = d.execute("SELECT m.name, COALESCE(SUM(a.status IN ('present','late')),0) p, COALESCE(SUM(a.status!='excused'),0) t "
                       "FROM members m JOIN attendance a ON a.member_id=m.id WHERE m.active=1 GROUP BY m.id "
                       "HAVING t>0 ORDER BY CAST(p AS REAL)/t DESC, m.name").fetchall()
    mix = {r[0]: r[1] for r in d.execute("SELECT status, COUNT(*) FROM attendance GROUP BY status")}
    pj = d.execute("SELECT status, COUNT(*) n, COALESCE(SUM(budget),0) b FROM projects GROUP BY status").fetchall()
    byunit = d.execute("SELECT u.name, COALESCE(SUM(a.status IN ('present','late')),0) p, COALESCE(SUM(a.status!='excused'),0) t, "
                       "COUNT(DISTINCT m.id) n FROM units u JOIN member_units mu ON mu.unit_id=u.id "
                       "JOIN members m ON m.id=mu.member_id AND m.active=1 LEFT JOIN attendance a ON a.member_id=m.id "
                       "GROUP BY u.id ORDER BY u.name").fetchall()
    fin = d.execute("SELECT kind, COUNT(*) n, COALESCE(SUM(amount),0) s FROM payments WHERE voided=0 GROUP BY kind").fetchall() if can("finance") else []
    return render_template("reports.html", trend=trend, people=people, mix=mix, pj=pj, fin=fin, att=ATT, money=money, byunit=byunit)

def safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in "=+-@\t\r" else v

def csv_response(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([safe(c) for c in r])
    log(f"exported {name}")
    db().commit()
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})

@app.route("/export/members.csv")
@need("members_view")
def export_members():
    rows = db().execute("SELECT name,email,phone,department,level,position,(SELECT GROUP_CONCAT(u.name, '; ') FROM member_units mu JOIN units u ON u.id=mu.unit_id WHERE mu.member_id=members.id),student_id,date_joined,emergency_name,emergency_phone,role,active FROM members ORDER BY name")
    return csv_response("members.csv", ["Name", "Email", "Phone", "Academic department", "Level", "Position", "Units", "Student ID", "Date joined", "Emergency contact", "Emergency phone", "Portfolio", "Active"], rows)

@app.route("/export/attendance.csv")
@need("attendance_view")
def export_attendance():
    rows = db().execute("SELECT mt.held_on, mt.title, m.name, UPPER(SUBSTR(a.status,1,1))||SUBSTR(a.status,2) "
                        "FROM attendance a JOIN meetings mt ON mt.id=a.meeting_id JOIN members m ON m.id=a.member_id "
                        "ORDER BY mt.held_on DESC, m.name")
    return csv_response("attendance.csv", ["Date", "Meeting", "Member", "Status"], rows)

@app.route("/export/payments.csv")
@need("finance")
def export_payments():
    rows = db().execute("SELECT p.receipt_no, p.paid_on, m.name, p.kind, p.description, p.amount, p.method, p.recorded_by, "
                        "CASE p.voided WHEN 1 THEN 'Voided' ELSE 'Valid' END FROM payments p JOIN members m ON m.id=p.member_id ORDER BY p.id DESC")
    return csv_response("payments.csv", ["Receipt", "Date", "Member", "Type", "Description", "Amount", "Method", "Processed by", "State"], rows)

@app.route("/audit")
@need(top=True)
def audit():
    rows = db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300").fetchall()
    return render_template("audit.html", rows=rows)

# ---------- CLI ----------
@app.cli.command("create-admin")
@click.option("--name", prompt=True)
@click.option("--email", prompt=True)
@click.password_option()
def create_admin(name, email, password):
    """Create the first administrator (e.g. the President)."""
    if len(password) < 10:
        raise SystemExit("Password must be at least 10 characters.")
    init_db()
    d = sqlite3.connect(DB_PATH)
    d.execute("INSERT INTO members(name,email,position,role,pw_hash,must_change) VALUES(?,?,?,?,?,0)",
              (name, email.lower(), "President", "president", generate_password_hash(password)))
    d.commit()
    print("Admin created. Run the app and sign in.")

init_db()

if __name__ == "__main__":
    app.run(debug=False)
