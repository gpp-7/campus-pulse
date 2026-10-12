import os
import math
import random
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import mysql.connector
import bcrypt
import jwt

app = Flask(__name__)
CORS(app)

JWT_SECRET = "gpp7_super_secret_key_patna_2026"
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Sarvjit@71#",  # <-- Apna MySQL password dalein
    "database": "gpp7_portal"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_DIR = os.path.join(BASE_DIR, "notes")
if not os.path.exists(NOTES_DIR):
    os.makedirs(NOTES_DIR, exist_ok=True)

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def init_db():
    """Ensure schema tables & fix column truncation issue."""
    try:
        conn = get_db()
        cursor = conn.cursor()

        # Assignments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                faculty_id INT NOT NULL,
                branch VARCHAR(100) NOT NULL,
                semester INT NOT NULL,
                work_type VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT NULL,
                deadline DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (faculty_id) REFERENCES faculty(id) ON DELETE CASCADE
            )
        """)

        # Ensure is_approved is TINYINT(1) to avoid Error 1265
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'faculty' AND COLUMN_NAME = 'is_approved'
        """, (DB_CONFIG['database'],))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE faculty ADD COLUMN is_approved TINYINT(1) NOT NULL DEFAULT 0")
            print("--> [DB INIT] Added is_approved column.")
        else:
            # Modify to TINYINT(1) in case it was created with wrong type
            cursor.execute("ALTER TABLE faculty MODIFY COLUMN is_approved TINYINT(1) NOT NULL DEFAULT 0")

        # Attendance sessions additions
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'attendance_sessions' AND COLUMN_NAME = 'branch'
        """, (DB_CONFIG['database'],))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE attendance_sessions ADD COLUMN branch VARCHAR(100) NOT NULL DEFAULT 'All'")
            cursor.execute("ALTER TABLE attendance_sessions ADD COLUMN semester INT NOT NULL DEFAULT 1")

        cursor.execute("ALTER TABLE faculty MODIFY COLUMN current_status ENUM('Free', 'In Class', 'In Meeting', 'On Leave', 'Other') DEFAULT 'Free'")

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("DB Init Error:", str(e))

init_db()

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def verify_token(req, allowed_roles=None):
    auth_header = req.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return None, ("Token missing or invalid", 401)
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if allowed_roles and payload.get('role') not in allowed_roles:
            return None, ("Permission Denied", 403)
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, ("Session Expired. Please login again.", 401)
    except Exception:
        return None, ("Invalid Token", 401)

def resolve_canonical_branch_folder(branch_name):
    b = str(branch_name).lower().strip()
    if "civil" in b:
        return "Civil Engg"
    if "electron" in b or "ece" in b or b == "ec":
        return "Electronics Engg"
    if "electr" in b or "eee" in b or b == "ee":
        return "Electrical Engg"
    if "mech" in b or b == "me":
        return "Mechanical Engg"
    if "comp" in b or "cse" in b or b == "cs":
        return "Computer Science & Engg"
    return None

# ----------------- REGISTRATION ENDPOINTS -----------------

@app.route('/api/register/student', methods=['POST'])
def register_student():
    data = request.json or {}
    name = data.get('name')
    enrollment = data.get('enrollmentNumber')
    branch = data.get('branch')
    semester = data.get('semester')
    email = data.get('email')
    password = data.get('password')

    if not all([name, enrollment, branch, semester, email, password]):
        return jsonify({"message": "All fields are required"}), 400

    hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(12)).decode('utf-8')

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return jsonify({"message": "Email already exists"}), 409

        cursor.execute("SELECT id FROM students WHERE enrollment_number = %s", (enrollment,))
        if cursor.fetchone():
            return jsonify({"message": "Enrollment number already exists"}), 409

        cursor.execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'student')",
                       (name, email, hashed_pw))
        user_id = cursor.lastrowid

        cursor.execute("INSERT INTO students (user_id, enrollment_number, branch, semester) VALUES (%s, %s, %s, %s)",
                       (user_id, enrollment, branch, int(semester)))
        conn.commit()
        return jsonify({"message": "Student registered successfully!"}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"message": "Database error", "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# FACULTY REGISTRATION (FIXED: Pass Integer 0 Instead of Python False)
@app.route('/api/register/faculty', methods=['POST'])
def register_faculty():
    data = request.json or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    designation = data.get('designation')
    department = data.get('department')
    cabin = data.get('cabinNumber')

    if not all([name, email, password, designation, department, cabin]):
        return jsonify({"message": "All fields are required"}), 400

    hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(12)).decode('utf-8')
    faculty_code = f"FAC-{random.randint(1000, 9999)}"

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return jsonify({"message": "Email already exists"}), 409

        cursor.execute("INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, 'faculty')",
                       (name, email, hashed_pw))
        user_id = cursor.lastrowid

        # is_approved = 0 (Integer) completely avoids MySQL Error 1265 truncation
        cursor.execute("""
            INSERT INTO faculty (user_id, faculty_code, designation, department, cabin_number, is_approved)
            VALUES (%s, %s, %s, %s, %s, 0)
        """, (user_id, faculty_code, designation, department, cabin))

        conn.commit()
        print(f"--> [SUCCESS] Faculty registered: {name} ({faculty_code}) Pending approval.")
        return jsonify({"message": "Registration submitted! Admin approval required before you can log in."}), 201

    except Exception as e:
        conn.rollback()
        print("Faculty Register Error:", str(e))
        return jsonify({"message": "Database error: " + str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------- AUTHENTICATION (LOGIN) -----------------

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    identifier = data.get('identifier', '').strip()
    password = data.get('password', '')
    role = data.get('role')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        user = None
        if role == 'student':
            cursor.execute("""
                SELECT u.id, u.name, u.email, u.password_hash, u.role,
                       s.id AS student_id, s.enrollment_number, s.branch, s.semester
                FROM users u
                JOIN students s ON u.id = s.user_id
                WHERE s.enrollment_number = %s AND u.role = 'student'
            """, (identifier,))
            user = cursor.fetchone()
        elif role == 'faculty':
            cursor.execute("""
                SELECT u.id, u.name, u.email, u.password_hash, u.role,
                       f.id AS faculty_id, f.faculty_code, f.designation, f.department, f.cabin_number, f.current_status, f.is_approved
                FROM users u
                JOIN faculty f ON u.id = f.user_id
                WHERE u.email = %s AND u.role = 'faculty'
            """, (identifier,))
            user = cursor.fetchone()

            # Approval check: integer 1 or boolean True check
            if user and int(user.get('is_approved') or 0) != 1:
                return jsonify({"message": "Aapka account admin dwara approved nahi hua hai. Kripya admin approval ka intezar karein."}), 403

        elif role == 'admin':
            cursor.execute("""
                SELECT id, name, email, password_hash, role
                FROM users
                WHERE email = %s AND role = 'admin'
            """, (identifier,))
            user = cursor.fetchone()

        if not user or not bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
            return jsonify({"message": "Invalid Credentials"}), 401

        profile_id_val = user.get('student_id') or user.get('faculty_id') or user['id']

        payload = {
            "user_id": user['id'],
            "role": user['role'],
            "name": user['name'],
            "profile_id": profile_id_val,
            "exp": datetime.now(timezone.utc) + timedelta(hours=10)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        user.pop('password_hash', None)

        return jsonify({"message": "Login successful", "token": token, "user": user}), 200
    finally:
        cursor.close()
        conn.close()

# ----------------- ADMIN APPROVAL ENDPOINTS -----------------

@app.route('/api/admin/pending-faculties', methods=['GET'])
def get_pending_faculties():
    payload, err = verify_token(request, ['admin'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT f.id AS faculty_id, u.id AS user_id, u.name, u.email, f.faculty_code,
                   f.designation, f.department, f.cabin_number, u.created_at
            FROM faculty f
            JOIN users u ON f.user_id = u.id
            WHERE f.is_approved = 0 OR f.is_approved IS NULL
            ORDER BY f.id DESC
        """)
        pending = cursor.fetchall()
        for p in pending:
            if isinstance(p.get('created_at'), datetime):
                p['created_at'] = p['created_at'].strftime("%d %b %Y, %I:%M %p")
        return jsonify({"pending_faculties": pending}), 200
    finally:
        cursor.close()
        conn.close()

@app.route('/api/admin/approve-faculty/<int:faculty_id>', methods=['POST'])
def approve_faculty(faculty_id):
    payload, err = verify_token(request, ['admin'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE faculty SET is_approved = 1 WHERE id = %s", (faculty_id,))
        conn.commit()
        return jsonify({"message": "Faculty access granted successfully!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"message": "Database error", "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/admin/reject-faculty/<int:faculty_id>', methods=['DELETE'])
def reject_faculty(faculty_id):
    payload, err = verify_token(request, ['admin'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT user_id FROM faculty WHERE id = %s", (faculty_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "Faculty record not found!"}), 404

        user_id = row['user_id']
        cursor.execute("DELETE FROM faculty WHERE id = %s", (faculty_id,))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"message": "Registration cancelled and details deleted permanently!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"message": "Database error", "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------- FACULTY OPERATIONS -----------------

@app.route('/api/faculty/status', methods=['POST'])
def update_status():
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]

    new_status = request.json.get('status')
    if new_status not in ['Free', 'In Class', 'In Meeting', 'On Leave', 'Other']:
        return jsonify({"message": "Invalid status"}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE faculty SET current_status = %s WHERE id = %s", (new_status, payload['profile_id']))
        conn.commit()
        return jsonify({"message": "Status updated successfully", "status": new_status})
    finally:
        cursor.close()
        conn.close()

@app.route('/api/faculty/attendance/create', methods=['POST'])
def create_attendance():
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]

    lat = request.json.get('lat')
    lng = request.json.get('lng')
    branch = request.json.get('branch')
    semester = request.json.get('semester')

    if lat is None or lng is None:
        return jsonify({"message": "Coordinates required"}), 400
    if not branch or not semester:
        return jsonify({"message": "Branch aur Semester select karna anivarya hai!"}), 400

    token = f"GPP7-{random.randint(100000, 999999)}"
    expires_at = datetime.now() + timedelta(minutes=5)

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE attendance_sessions SET is_active = FALSE WHERE faculty_id = %s", (payload['profile_id'],))
        cursor.execute("""
            INSERT INTO attendance_sessions (faculty_id, token, branch, semester, latitude, longitude, expires_at, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        """, (payload['profile_id'], token, str(branch).strip(), int(semester), lat, lng, expires_at))
        conn.commit()
        return jsonify({
            "token": token, 
            "branch": branch,
            "semester": semester,
            "expires_at": expires_at.isoformat()
        })
    finally:
        cursor.close()
        conn.close()

# ----------------- LIVE ACTIVE ATTENDANCE RADAR FOR STUDENT -----------------
@app.route('/api/student/attendance/active-session', methods=['GET'])
def get_student_active_attendance_session():
    payload, err = verify_token(request, ['student'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, branch, semester FROM students WHERE id = %s OR user_id = %s", 
                       (payload.get('profile_id'), payload.get('user_id')))
        stud = cursor.fetchone()
        if not stud:
            return jsonify({"active_session": None}), 200

        stud_id = stud['id']
        stud_branch = str(stud['branch']).strip()
        stud_sem = int(stud['semester'])
        stud_canon = resolve_canonical_branch_folder(stud_branch)

        cursor.execute("""
            SELECT ses.id, ses.token, ses.branch, ses.semester, ses.expires_at,
                   u.name AS faculty_name, f.designation, f.department
            FROM attendance_sessions ses
            JOIN faculty f ON ses.faculty_id = f.id
            JOIN users u ON f.user_id = u.id
            WHERE ses.is_active = TRUE AND ses.expires_at > NOW()
            ORDER BY ses.id DESC
        """)
        active_sessions = cursor.fetchall()

        matched_session = None
        for s in active_sessions:
            s_branch = str(s['branch']).strip()
            s_sem = int(s['semester'])
            s_canon = resolve_canonical_branch_folder(s_branch)

            if s_sem == stud_sem:
                if (s_branch.lower() == 'all' or 
                    s_branch.lower() == stud_branch.lower() or 
                    (s_canon and stud_canon and s_canon == stud_canon)):
                    
                    cursor.execute("SELECT id FROM attendance_logs WHERE session_id = %s AND student_id = %s", (s['id'], stud_id))
                    already_marked = cursor.fetchone() is not None

                    matched_session = {
                        "session_id": s['id'],
                        "token": s['token'],
                        "faculty_name": s['faculty_name'],
                        "designation": s['designation'],
                        "department": s['department'],
                        "expires_at": s['expires_at'].isoformat(),
                        "already_marked": already_marked
                    }
                    break

        return jsonify({"active_session": matched_session}), 200
    finally:
        cursor.close()
        conn.close()

# ----------------- STUDENT ATTENDANCE MARK -----------------
@app.route('/api/student/attendance/mark', methods=['POST'])
def mark_attendance():
    payload, err = verify_token(request, ['student'])
    if err: return jsonify({"message": err[0]}), err[1]

    stud_lat = request.json.get('lat')
    stud_lng = request.json.get('lng')
    token = request.json.get('token', '').strip()

    if stud_lat is None or stud_lng is None:
        return jsonify({"message": "Location coordinates required!"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, branch, semester FROM students WHERE id = %s OR user_id = %s", 
                       (payload.get('profile_id'), payload.get('user_id')))
        stud = cursor.fetchone()
        if not stud:
            return jsonify({"message": "Student record not found!"}), 404

        student_id = stud['id']
        stud_branch = str(stud['branch']).strip()
        stud_sem = int(stud['semester'])
        stud_canon = resolve_canonical_branch_folder(stud_branch)

        session = None
        if token:
            cursor.execute("""
                SELECT id, faculty_id, branch, semester, latitude, longitude, expires_at 
                FROM attendance_sessions 
                WHERE token = %s AND is_active = TRUE
            """, (token,))
            session = cursor.fetchone()
        else:
            cursor.execute("""
                SELECT id, faculty_id, branch, semester, latitude, longitude, expires_at 
                FROM attendance_sessions 
                WHERE is_active = TRUE AND expires_at > NOW()
                ORDER BY id DESC
            """)
            sessions = cursor.fetchall()
            for s in sessions:
                s_branch = str(s['branch']).strip()
                s_sem = int(s['semester'])
                s_canon = resolve_canonical_branch_folder(s_branch)
                if s_sem == stud_sem and (s_branch.lower() == 'all' or s_branch.lower() == stud_branch.lower() or (s_canon and stud_canon and s_canon == stud_canon)):
                    session = s
                    break

        if not session: 
            return jsonify({"message": "Koi active attendance session nahi mila!"}), 404
        if datetime.now() > session['expires_at']: 
            return jsonify({"message": "Attendance session expire ho chuka hai!"}), 410

        dist = haversine_distance(float(stud_lat), float(stud_lng), float(session['latitude']), float(session['longitude']))
        if dist > 50.0: 
            return jsonify({"message": f"Faculty ke 50m allowable boundary se bahar hain. Duri: {int(dist)}m"}), 403

        cursor.execute("INSERT INTO attendance_logs (session_id, student_id, recorded_distance_m) VALUES (%s, %s, %s)",
                       (session['id'], student_id, int(dist)))
        conn.commit()
        return jsonify({"message": f"Attendance successfully marked! ({int(dist)}m proximity verified)"})

    except mysql.connector.Error as err_db:
        if err_db.errno == 1062: 
            return jsonify({"message": "Aap pehle hi attendance mark kar chuke hain!"}), 409
        return jsonify({"message": "Database Error: " + str(err_db)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------- FACULTY ASSIGNMENTS API -----------------
@app.route('/api/faculty/assignments/create', methods=['POST'])
def create_assignment():
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]

    data = request.json or {}
    branch = data.get('branch')
    semester = data.get('semester')
    work_type = data.get('work_type')
    title = data.get('title')
    deadline = data.get('deadline')
    description = data.get('description') or ''

    if not all([branch, semester, work_type, title, deadline]):
        return jsonify({"message": "All required fields must be filled!"}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM faculty WHERE id = %s OR user_id = %s", (payload.get('profile_id'), payload.get('user_id')))
        fac = cursor.fetchone()
        faculty_id = fac['id'] if fac else payload.get('profile_id')

        cursor.execute("""
            INSERT INTO assignments (faculty_id, branch, semester, work_type, title, description, deadline)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (int(faculty_id), str(branch).strip(), int(semester), str(work_type).strip(), str(title).strip(), str(description).strip(), str(deadline).strip()))
        conn.commit()
        return jsonify({"message": "Assignment / Work published successfully!"}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"message": "Database error: " + str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/faculty/assignments', methods=['GET'])
def get_faculty_assignments():
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM faculty WHERE id = %s OR user_id = %s", (payload.get('profile_id'), payload.get('user_id')))
        fac = cursor.fetchone()
        faculty_id = fac['id'] if fac else payload.get('profile_id')

        cursor.execute("""
            SELECT id, branch, semester, work_type, title, deadline, created_at
            FROM assignments
            WHERE faculty_id = %s
            ORDER BY id DESC
        """, (faculty_id,))
        assignments = cursor.fetchall()
        for item in assignments:
            if isinstance(item.get('deadline'), (datetime,)):
                item['deadline'] = item['deadline'].strftime("%d %b %Y")
            elif item.get('deadline'):
                item['deadline'] = str(item['deadline'])
            if isinstance(item.get('created_at'), (datetime,)):
                item['created_at'] = item['created_at'].strftime("%d %b %Y, %I:%M %p")
        return jsonify({"assignments": assignments}), 200
    except Exception as e:
        return jsonify({"message": "Database error", "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/faculty/assignments/<int:assignment_id>', methods=['DELETE'])
def delete_faculty_assignment(assignment_id):
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM faculty WHERE id = %s OR user_id = %s", (payload.get('profile_id'), payload.get('user_id')))
        fac = cursor.fetchone()
        faculty_id = fac['id'] if fac else payload.get('profile_id')

        cursor.execute("DELETE FROM assignments WHERE id = %s AND faculty_id = %s", (assignment_id, faculty_id))
        conn.commit()
        return jsonify({"message": "Assignment deleted successfully!"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"message": "Database error", "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------- STUDENT ASSIGNMENTS API -----------------
@app.route('/api/student/assignments', methods=['GET'])
def get_student_assignments():
    payload, err = verify_token(request, ['student'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, user_id, enrollment_number, branch, semester FROM students WHERE id = %s OR user_id = %s", 
                       (payload.get('profile_id'), payload.get('user_id')))
        stud = cursor.fetchone()
        if not stud:
            return jsonify({"assignments": []}), 200

        stud_branch = str(stud['branch']).strip()
        stud_sem = int(stud['semester'])
        stud_canon = resolve_canonical_branch_folder(stud_branch)

        cursor.execute("SELECT a.id, a.work_type, a.title, a.description, a.deadline, a.created_at, a.branch, a.semester, u.name AS faculty_name, f.designation, f.department FROM assignments a JOIN faculty f ON a.faculty_id = f.id JOIN users u ON f.user_id = u.id WHERE a.semester = %s ORDER BY a.deadline ASC, a.id DESC", (stud_sem,))
        all_sem = cursor.fetchall()

        matched = []
        for a in all_sem:
            assign_branch = str(a['branch']).strip()
            assign_canon = resolve_canonical_branch_folder(assign_branch)
            if (assign_canon and stud_canon and assign_canon == stud_canon) or (assign_branch.lower() == stud_branch.lower()):
                dl = a.get('deadline')
                dl_str = dl.strftime("%d %b %Y") if isinstance(dl, datetime) else str(dl)
                ca = a.get('created_at')
                ca_str = ca.strftime("%d %b %Y, %I:%M %p") if isinstance(ca, datetime) else str(ca)
                matched.append({
                    "id": a['id'],
                    "work_type": a['work_type'],
                    "title": a['title'],
                    "description": a['description'] or '',
                    "deadline": dl_str,
                    "created_at": ca_str,
                    "faculty_name": a['faculty_name'],
                    "designation": a['designation'],
                    "department": a['department'],
                    "branch": a['branch'],
                    "semester": a['semester']
                })

        return jsonify({"assignments": matched, "student_branch": stud_branch, "student_semester": stud_sem}), 200
    finally:
        cursor.close()
        conn.close()

# ----------------- STRICT BRANCH & SEMESTER NOTES API -----------------
@app.route('/api/student/notes', methods=['GET'])
def get_student_notes():
    payload, err = verify_token(request, ['student'])
    if err: return jsonify({"message": err[0]}), err[1]

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT branch, semester FROM students WHERE id = %s OR user_id = %s",
                       (payload.get('profile_id'), payload.get('user_id')))
        stud = cursor.fetchone()
        if not stud:
            return jsonify({"notes": [], "folder_path": ""}), 200

        stud_branch = str(stud['branch']).strip()
        stud_sem = str(stud['semester']).strip()

        canonical_folder_name = resolve_canonical_branch_folder(stud_branch)
        if not canonical_folder_name:
            return jsonify({"notes": [], "branch": stud_branch, "semester": f"Sem {stud_sem}"}), 200

        target_branch_path = os.path.join(NOTES_DIR, canonical_folder_name)
        target_sem_folder = f"Sem {stud_sem}"
        notes_target_path = os.path.join(target_branch_path, target_sem_folder)

        notes_list = []
        if os.path.exists(notes_target_path):
            for fname in os.listdir(notes_target_path):
                f_full = os.path.join(notes_target_path, fname)
                if os.path.isfile(f_full):
                    size_bytes = os.path.getsize(f_full)
                    size_kb = round(size_bytes / 1024, 1)
                    size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 2)} MB"
                    ext = os.path.splitext(fname)[1].lower()
                    
                    rel_p = f"{canonical_folder_name}/{target_sem_folder}/{fname}"
                    notes_list.append({
                        "filename": fname,
                        "file_size": size_str,
                        "extension": ext.replace('.', '').upper() or 'FILE',
                        "relative_path": rel_p
                    })

        return jsonify({
            "notes": notes_list,
            "branch": canonical_folder_name,
            "semester": target_sem_folder
        }), 200
    finally:
        cursor.close()
        conn.close()

@app.route('/api/notes/download/<path:filepath>', methods=['GET'])
def download_note_file(filepath):
    try:
        return send_from_directory(NOTES_DIR, filepath, as_attachment=False)
    except Exception:
        return jsonify({"message": "File not found or inaccessible"}), 404

# ----------------- FACULTY ATTENDANCE BATCH LOGS -----------------
@app.route('/api/faculty/attendance/grouped-sessions', methods=['GET'])
def get_faculty_grouped_sessions():
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM faculty WHERE id = %s OR user_id = %s", (payload.get('profile_id'), payload.get('user_id')))
        fac = cursor.fetchone()
        faculty_id = fac['id'] if fac else payload.get('profile_id')

        cursor.execute("""
            SELECT id, token, branch, semester, expires_at, is_active 
            FROM attendance_sessions 
            WHERE faculty_id = %s 
            ORDER BY id DESC
        """, (faculty_id,))
        sessions = cursor.fetchall()
        if not sessions:
            return jsonify({"sessions": []}), 200

        session_ids = [s['id'] for s in sessions]
        format_strings = ','.join(['%s'] * len(session_ids))
        query = f"""
            SELECT al.session_id, u.name AS student_name, s.enrollment_number, s.branch, al.recorded_distance_m, al.logged_at
            FROM attendance_logs al
            INNER JOIN students s ON al.student_id = s.id
            INNER JOIN users u ON s.user_id = u.id
            WHERE al.session_id IN ({format_strings})
            ORDER BY al.logged_at DESC
        """
        cursor.execute(query, tuple(session_ids))
        logs = cursor.fetchall()
        grouped_data = []
        now = datetime.now()
        for s in sessions:
            s_logs = []
            for l in logs:
                if l['session_id'] == s['id']:
                    t_val = l['logged_at']
                    s_logs.append({
                        "student_name": l['student_name'],
                        "enrollment_number": l['enrollment_number'],
                        "branch": l['branch'],
                        "recorded_distance_m": l['recorded_distance_m'],
                        "timestamp": t_val.strftime("%I:%M:%S %p") if isinstance(t_val, datetime) else str(t_val)
                    })
            grouped_data.append({
                "session_id": s['id'],
                "token": s['token'],
                "branch": s.get('branch') or 'All',
                "semester": s.get('semester') or 0,
                "is_expired": now > s['expires_at'],
                "total_students": len(s_logs),
                "students": s_logs
            })
        return jsonify({"sessions": grouped_data}), 200
    finally:
        cursor.close()
        conn.close()

@app.route('/api/faculty/attendance/session/<int:session_id>', methods=['DELETE'])
def delete_faculty_attendance_session(session_id):
    payload, err = verify_token(request, ['faculty'])
    if err: return jsonify({"message": err[0]}), err[1]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("DELETE FROM attendance_logs WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM attendance_sessions WHERE id = %s", (session_id,))
        conn.commit()
        return jsonify({"message": "Session deleted permanently!"}), 200
    finally:
        cursor.close()
        conn.close()

@app.route('/api/faculties', methods=['GET'])
def list_faculties():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.name, f.faculty_code, f.designation, f.department, f.cabin_number, f.current_status
            FROM faculty f JOIN users u ON f.user_id = u.id 
            WHERE f.is_approved = 1
            ORDER BY u.name ASC
        """)
        return jsonify({"faculties": cursor.fetchall()})
    finally:
        cursor.close()
        conn.close()

# ----------------- ADMIN DATA -----------------
@app.route('/api/admin/data', methods=['GET'])
def get_admin_data():
    payload, err = verify_token(request, ['admin'])
    if err: return jsonify({"message": err[0]}), err[1]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT s.enrollment_number, u.name, u.email, s.branch, s.semester, u.id as user_id 
            FROM students s JOIN users u ON s.user_id = u.id ORDER BY u.id DESC
        """)
        students = cursor.fetchall()
        cursor.execute("""
            SELECT f.id as faculty_id, f.faculty_code, u.name, u.email, f.designation, f.department, 
                   f.cabin_number, f.current_status, f.is_approved, u.id as user_id 
            FROM faculty f JOIN users u ON f.user_id = u.id ORDER BY u.id DESC
        """)
        faculties = cursor.fetchall()
        cursor.execute("""
            SELECT u_stud.name AS student_name, s.enrollment_number, s.branch, u_fac.name AS faculty_name, al.recorded_distance_m, al.logged_at
            FROM attendance_logs al
            JOIN attendance_sessions ses ON al.session_id = ses.id
            JOIN faculty f ON ses.faculty_id = f.id
            JOIN users u_fac ON f.user_id = u_fac.id
            JOIN students s ON al.student_id = s.id
            JOIN users u_stud ON s.user_id = u_stud.id
            ORDER BY al.logged_at DESC
        """)
        logs = cursor.fetchall()
        for l in logs:
            t = l['logged_at']
            l['timestamp'] = t.strftime("%d %b %Y, %I:%M:%S %p") if isinstance(t, datetime) else str(t)
        return jsonify({"students": students, "faculties": faculties, "logs": logs}), 200
    finally:
        cursor.close()
        conn.close()

@app.route('/api/admin/delete-user/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    payload, err = verify_token(request, ['admin'])
    if err: return jsonify({"message": err[0]}), err[1]
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"message": "User deleted successfully!"}), 200
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)