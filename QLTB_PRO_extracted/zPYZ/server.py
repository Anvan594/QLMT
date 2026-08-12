# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'server.py'
# Bytecode version: 3.14rc3 (3627)
# Source timestamp: 2026-08-11 15:58:57 UTC (1786463937)

"""\nQLTB PRO - Local backend server\nChạy hoàn toàn offline trên máy tính của bạn.\nChỉ dùng thư viện chuẩn của Python (không cần pip install).\n\nCách chạy:\n    python3 server.py\nSau đó mở trình duyệt tại: http://127.0.0.1:8877/\n\nDữ liệu được lưu vào file qltb.db nằm cùng thư mục với script.\n"""
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from itertools import zip_longest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
try:
    import qrgen
except ImportError:
    class _QRGenFallback:
        EC_M = None
        @staticmethod
        def generate_qr_matrix(*args, **kwargs):
            raise RuntimeError('Thiếu qrgen.py - chức năng QR chưa khả dụng')
    qrgen = _QRGenFallback()
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    STATIC_INDEX = os.path.join(sys._MEIPASS, 'index.html')
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    STATIC_INDEX = os.path.join(BASE_DIR, 'index.html')
DB_PATH = os.path.join(BASE_DIR, 'qltb.db')
PORT = 8877
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn
def table_exists(conn, name):
    row = conn.execute('SELECT name FROM sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone()
    return row is not None
def get_columns(conn, table):
    """Trả về set tên cột hiện có của bảng (dựa trên PRAGMA table_info)."""
    rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    return {r['name'] for r in rows}
def add_column_if_missing(conn, table, column, sqlite_type='TEXT'):
    if column in get_columns(conn, table):
        return
    else:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {sqlite_type}')
        conn.commit()
def upsert_kv(conn, table, key, value):
    """Thay cho \'INSERT ... ON CONFLICT(key) DO UPDATE ...\' để tương thích rộng."""
    row = conn.execute(f'SELECT [key] FROM {table} WHERE [key]=?', (key,)).fetchone()
    if row is not None:
        conn.execute(f'UPDATE {table} SET value=? WHERE [key]=?', (value, key))
    else:
        conn.execute(f'INSERT INTO {table}([key],value) VALUES(?,?)', (key, value))
def exec_multi_statement(conn, script):
    """Thay cho conn.executescript() — tách script thành từng câu lệnh riêng\nrồi chạy tuần tự (SQLite tự hỗ trợ IF NOT EXISTS)."""
    statements = [s.strip() for s in script.split(';') if s.strip()]
    for stmt in statements:
        conn.execute(stmt + ';')
    conn.commit()
SCHEMA = '\nCREATE TABLE IF NOT EXISTS departments(\n    id TEXT PRIMARY KEY,\n    name TEXT NOT NULL,\n    branch_id TEXT\n);\nCREATE TABLE IF NOT EXISTS branches(\n    id TEXT PRIMARY KEY,\n    name TEXT NOT NULL,\n    address TEXT,\n    phone TEXT,\n    note TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS borrows(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    device_id TEXT NOT NULL,\n    borrower_name TEXT,\n    borrower_dept_id TEXT,\n    borrower_pos_id TEXT,\n    borrower_phone TEXT,\n    status TEXT,\n    borrow_date TEXT,\n    due_date TEXT,\n    return_date TEXT,\n    performer TEXT,\n    note TEXT,\n    prev_device_status TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS users(\n    id TEXT PRIMARY KEY,\n    full_name TEXT NOT NULL,\n    email TEXT,\n    phone TEXT,\n    dept_id TEXT,\n    position TEXT,\n    note TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS positions(\n    id TEXT PRIMARY KEY,\n    dept_id TEXT NOT NULL,\n    name TEXT NOT NULL\n);\nCREATE TABLE IF NOT EXISTS groups_tbl(\n    id TEXT PRIMARY KEY,\n    name TEXT NOT NULL\n);\nCREATE TABLE IF NOT EXISTS types_tbl(\n    id TEXT PRIMARY KEY,\n    group_id TEXT NOT NULL,\n    name TEXT NOT NULL\n);\nCREATE TABLE IF NOT EXISTS suppliers(\n    id TEXT PRIMARY KEY,\n    name TEXT NOT NULL,\n    tax_code TEXT,\n    email TEXT,\n    hotline TEXT,\n    website TEXT,\n    contact_person TEXT\n);\nCREATE TABLE IF NOT EXISTS devices(\n    id TEXT PRIMARY KEY,\n    asset_code TEXT,\n    dept_id TEXT,\n    pos_id TEXT,\n    group_id TEXT,\n    type_id TEXT,\n    model TEXT,\n    manufacturer TEXT,\n    serial TEXT,\n    config TEXT,\n    status TEXT,\n    import_date TEXT,\n    allocate_date TEXT,\n    warranty_months TEXT,\n    warranty_unit TEXT,\n    supplier TEXT,\n    value REAL,\n    note TEXT,\n    created_at INTEGER,\n    computer_name TEXT,\n    mainboard TEXT,\n    cpu TEXT,\n    ram TEXT,\n    gpu TEXT,\n    storage TEXT,\n    os_name TEXT,\n    win_key_bios TEXT,\n    win_key_current TEXT,\n    system_model TEXT,\n    serial_number TEXT,\n    mac_physical TEXT,\n    mac_virtual TEXT,\n    user_id TEXT\n);\nCREATE TABLE IF NOT EXISTS history(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    type TEXT,\n    date TEXT,\n    device_ids TEXT,\n    device_names TEXT,\n    old_location TEXT,\n    new_location TEXT,\n    performer TEXT,\n    note TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS counters(\n    [key] TEXT PRIMARY KEY,\n    value INTEGER\n);\nCREATE TABLE IF NOT EXISTS settings(\n    [key] TEXT PRIMARY KEY,\n    value TEXT\n);\nCREATE TABLE IF NOT EXISTS materials(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    name TEXT NOT NULL,\n    category TEXT,\n    unit TEXT,\n    min_stock REAL,\n    supplier TEXT,\n    note TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS material_txn(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    type TEXT,\n    material_id TEXT NOT NULL,\n    material_name TEXT,\n    quantity REAL,\n    unit TEXT,\n    date TEXT,\n    dept_id TEXT,\n    pos_id TEXT,\n    supplier TEXT,\n    reason TEXT,\n    performer TEXT,\n    note TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS maintenance(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    device_id TEXT NOT NULL,\n    type TEXT,\n    issue TEXT,\n    status TEXT,\n    start_date TEXT,\n    expected_date TEXT,\n    complete_date TEXT,\n    performer TEXT,\n    cost REAL,\n    note TEXT,\n    prev_device_status TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS accounts(\n    id TEXT PRIMARY KEY,\n    username TEXT NOT NULL UNIQUE,\n    password_hash TEXT NOT NULL,\n    full_name TEXT,\n    role TEXT,\n    status TEXT,\n    note TEXT,\n    created_at INTEGER,\n    last_login INTEGER\n);\nCREATE TABLE IF NOT EXISTS logs(\n    id TEXT PRIMARY KEY,\n    module TEXT,\n    action TEXT,\n    detail TEXT,\n    actor TEXT,\n    created_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS audit_batches(\n    id TEXT PRIMARY KEY,\n    code TEXT,\n    name TEXT NOT NULL,\n    scope_type TEXT,\n    scope_dept_id TEXT,\n    scope_group_id TEXT,\n    status TEXT,\n    due_date TEXT,\n    note TEXT,\n    created_by TEXT,\n    created_at INTEGER,\n    completed_at INTEGER\n);\nCREATE TABLE IF NOT EXISTS audit_items(\n    id TEXT PRIMARY KEY,\n    batch_id TEXT NOT NULL,\n    device_id TEXT,\n    asset_code TEXT,\n    snap_model TEXT,\n    snap_manufacturer TEXT,\n    snap_serial TEXT,\n    snap_dept_id TEXT,\n    snap_dept_name TEXT,\n    snap_pos_id TEXT,\n    snap_pos_name TEXT,\n    snap_group_name TEXT,\n    snap_type_name TEXT,\n    snap_status TEXT,\n    snap_user_name TEXT,\n    item_type TEXT,\n    scan_status TEXT,\n    location_match TEXT,\n    condition_match TEXT,\n    actual_dept_id TEXT,\n    actual_pos_id TEXT,\n    actual_status TEXT,\n    scanned_by TEXT,\n    scanned_at INTEGER,\n    note TEXT\n);\n'
DEVICE_HW_COLUMNS = ['computer_name', 'mainboard', 'cpu', 'ram', 'gpu', 'storage', 'os_name', 'win_key_bios', 'win_key_current', 'system_model', 'serial_number', 'mac_physical', 'mac_virtual', 'user_id']
def migrate_db(conn):
    existing = get_columns(conn, 'devices')
    for col in DEVICE_HW_COLUMNS:
        if col not in existing:
            add_column_if_missing(conn, 'devices', col, 'TEXT')
    if table_exists(conn, 'users'):
        user_cols_chk = get_columns(conn, 'users')
        if 'full_name' not in user_cols_chk:
            backup_name = f'users_legacy_{now_ms()}'
            conn.execute(f'ALTER TABLE users RENAME TO {backup_name}')
            conn.commit()
    exec_multi_statement(conn, '\n    CREATE TABLE IF NOT EXISTS users(\n        id TEXT PRIMARY KEY,\n        full_name TEXT NOT NULL,\n        email TEXT,\n        phone TEXT,\n        dept_id TEXT,\n        position TEXT,\n        note TEXT,\n        created_at INTEGER\n    );\n    ')
    conn.commit()
    add_column_if_missing(conn, 'users', 'pos_id', 'TEXT')
    exec_multi_statement(conn, '\n    CREATE TABLE IF NOT EXISTS audit_batches(\n        id TEXT PRIMARY KEY,\n        code TEXT,\n        name TEXT NOT NULL,\n        scope_type TEXT,\n        scope_dept_id TEXT,\n        scope_group_id TEXT,\n        status TEXT,\n        due_date TEXT,\n        note TEXT,\n        created_by TEXT,\n        created_at INTEGER,\n        completed_at INTEGER\n    );\n    CREATE TABLE IF NOT EXISTS audit_items(\n        id TEXT PRIMARY KEY,\n        batch_id TEXT NOT NULL,\n        device_id TEXT,\n        asset_code TEXT,\n        snap_model TEXT,\n        snap_manufacturer TEXT,\n        snap_serial TEXT,\n        snap_dept_id TEXT,\n        snap_dept_name TEXT,\n        snap_pos_id TEXT,\n        snap_pos_name TEXT,\n        snap_group_name TEXT,\n        snap_type_name TEXT,\n        snap_status TEXT,\n        snap_user_name TEXT,\n        item_type TEXT,\n        scan_status TEXT,\n        location_match TEXT,\n        condition_match TEXT,\n        actual_dept_id TEXT,\n        actual_pos_id TEXT,\n        actual_status TEXT,\n        scanned_by TEXT,\n        scanned_at INTEGER,\n        note TEXT\n    );\n    ')
    conn.commit()
    if not table_exists(conn, 'branches'):
        exec_multi_statement(conn, '\n        CREATE TABLE IF NOT EXISTS branches(\n            id TEXT PRIMARY KEY,\n            name TEXT NOT NULL,\n            address TEXT,\n            phone TEXT,\n            note TEXT,\n            created_at INTEGER\n        );\n        ')
    if not table_exists(conn, 'borrows'):
        exec_multi_statement(conn, '\n        CREATE TABLE IF NOT EXISTS borrows(\n            id TEXT PRIMARY KEY,\n            code TEXT,\n            device_id TEXT NOT NULL,\n            borrower_name TEXT,\n            borrower_dept_id TEXT,\n            borrower_pos_id TEXT,\n            borrower_phone TEXT,\n            status TEXT,\n            borrow_date TEXT,\n            due_date TEXT,\n            return_date TEXT,\n            performer TEXT,\n            note TEXT,\n            prev_device_status TEXT,\n            created_at INTEGER\n        );\n        ')
    add_column_if_missing(conn, 'departments', 'branch_id', 'TEXT')
    conn.commit()
def gen_audit_batch_code(conn):
    """Sinh mã đợt kiểm kê dạng KK-YYMMDD-NNN (NNN tăng dần trong ngày)."""
    prefix = 'KK-' + datetime.now().strftime('%y%m%d')
    row = conn.execute('SELECT COUNT(*) c FROM audit_batches WHERE code LIKE ?', (prefix + '%',)).fetchone()
    seq = (row['c'] or 0) + 1
    return f'{prefix}-{seq:03d}'
def hash_password(password, salt=None):
    if salt is None:
        salt = uuid.uuid4().hex
    h = hashlib.pbkdf2_hmac('sha256', (password or '').encode('utf-8'), salt.encode('utf-8'), 100000)
    return salt + '$' + h.hex()
def verify_password(password, stored):
    if not stored or '$' not in stored:
        return False
    else:
        salt, _ = stored.split('$', 1)
        return hash_password(password, salt) == stored
SESSIONS = {}
_local = threading.local()
def new_token():
    return uuid.uuid4().hex + uuid.uuid4().hex
def current_account():
    return getattr(_local, 'account', None)
def ensure_default_account(conn):
    """Đảm bảo luôn có ít nhất 1 tài khoản quản trị để đăng nhập lần đầu."""
    row = conn.execute('SELECT COUNT(*) c FROM accounts').fetchone()
    if row['c'] == 0:
        conn.execute('INSERT INTO accounts(id,username,password_hash,full_name,role,status,note,created_at,last_login) VALUES(?,?,?,?,?,?,?,?,?)', (new_id(), 'admin', hash_password('admin123'), 'Quản trị viên', 'admin', 'active', '', now_ms(), None))
        conn.commit()
    else:
        has_sa = conn.execute('SELECT COUNT(*) c FROM accounts WHERE role=\'superadmin\'').fetchone()['c']
        if has_sa:
            conn.execute('UPDATE accounts SET role=\'admin\' WHERE role=\'superadmin\'')
            conn.commit()
def ensure_warehouse_location(conn):
    """Đảm bảo luôn tồn tại Phòng/Ban \'Kho\' + Vị trí \'Kho\' — đây là nơi các\nthiết bị nhập kho nhưng chưa cấp phát được lưu tạm (module Kho)."""
    drow = conn.execute('SELECT id FROM departments WHERE name=\'Kho\'').fetchone()
    if drow:
        did = drow['id']
    else:
        did = new_id()
        conn.execute('INSERT INTO departments(id,name) VALUES(?,?)', (did, 'Kho'))
    prow = conn.execute('SELECT id FROM positions WHERE dept_id=? AND name=\'Kho\'', (did,)).fetchone()
    if not prow:
        conn.execute('INSERT INTO positions(id,dept_id,name) VALUES(?,?,?)', (new_id(), did, 'Kho'))
    conn.commit()
    return did
def init_db():
    conn = get_db()
    exec_multi_statement(conn, SCHEMA)
    conn.commit()
    migrate_db(conn)
    cur = conn.execute('SELECT COUNT(*) c FROM departments')
    if cur.fetchone()['c'] == 0:
        seed(conn)
    ensure_warehouse_location(conn)
    ensure_default_account(conn)
    conn.close()
def seed(conn):
    dept_seed = [('Văn phòng', ['Nhân sự', 'Kế Toán']), ('Kho', ['Kho']), ('Xưởng', ['Xưởng'])]
    dept_ids, pos_ids = ({}, {})
    for name, positions in dept_seed:
        did = new_id()
        dept_ids[name] = did
        conn.execute('INSERT INTO departments(id,name) VALUES(?,?)', (did, name))
        plist = positions if positions else [f'Khu vực {name}']
        for p in plist:
            pid = new_id()
            pos_ids[name, p] = pid
            conn.execute('INSERT INTO positions(id,dept_id,name) VALUES(?,?,?)', (pid, did, p))
    group_seed = {'Thiết bị CNTT': ['Máy tính', 'Máy in', 'Switch mạng', 'Camera an ninh'], 'Linh kiện CNTT': ['RAM', 'Ổ cứng SSD', 'Bàn phím', 'Chuột máy tính']}
    group_ids, type_ids = ({}, {})
    for gname, types in group_seed.items():
        gid = new_id()
        group_ids[gname] = gid
        conn.execute('INSERT INTO groups_tbl(id,name) VALUES(?,?)', (gid, gname))
        for t in types:
            tid = new_id()
            type_ids[gname, t] = tid
            conn.execute('INSERT INTO types_tbl(id,group_id,name) VALUES(?,?,?)', (tid, gid, t))
    cntt_id = group_ids['Thiết bị CNTT']
    maytinh_id = type_ids['Thiết bị CNTT', 'Máy tính']
    def mk(code, dept, pos, model, serial, import_date, warranty_months):
        did = dept_ids[dept]
        pid = pos_ids.get((dept, pos))
        maker = 'HP' if model.startswith('HP') else 'Dell'
        conn.execute(
            """INSERT INTO devices(
                id,asset_code,dept_id,pos_id,group_id,type_id,model,manufacturer,
                serial,config,status,import_date,allocate_date,warranty_months,warranty_unit,
                supplier,value,note,created_at,computer_name,mainboard,cpu,ram,gpu,storage,
                os_name,win_key_bios,win_key_current,system_model,serial_number,mac_physical,mac_virtual,user_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id(), code, did, pid, cntt_id, maytinh_id, model, maker,
             serial, '', 'Bình thường', import_date, '', warranty_months, 'Tháng',
             '', 0, '', now_ms(), '', '', '', '', '', '', '', '', '', '', serial, '', '', '')
        )
    conn.execute('INSERT INTO counters([key],value) VALUES(\'MT26\',4)')
    conn.commit()
def new_id():
    return uuid.uuid4().hex[:14]
def now_ms():
    return int(datetime.now().timestamp() * 1000)
def next_counter(conn, key):
    row = conn.execute('SELECT value FROM counters WHERE [key]=?', (key,)).fetchone()
    if row is None:
        conn.execute('INSERT INTO counters([key],value) VALUES(?,1)', (key,))
        return 1
    else:
        val = row['value'] + 1
        conn.execute('UPDATE counters SET value=? WHERE [key]=?', (val, key))
        return val
def initials(s):
    words = [w for w in (s or '').split() if w]
    letters = ''.join((w[0] for w in words)).upper()[:3]
    return letters or 'TB'
def gen_asset_code(conn, type_name):
    prefix = initials(type_name)
    yy = str(datetime.now().year)[(-2):]
    key = prefix + yy
    num = next_counter(conn, key)
    return f'{prefix}-{yy}{str(num).zfill(4)}'
def gen_doc_code(conn, kind):
    prefix = {'handover': 'BG', 'transfer': 'DC', 'reclaim': 'TH'}.get(kind, 'DC')
    yy = str(datetime.now().year)[(-2):]
    key = 'doc_' + kind
    num = next_counter(conn, key)
    return f'{prefix}-{yy}{str(num).zfill(4)}'
def gen_material_code(conn, kind):
    prefix = 'PNK' if kind == 'nhap' else 'PXK'
    yy = str(datetime.now().year)[(-2):]
    key = 'mtxn_' + kind
    num = next_counter(conn, key)
    return f'{prefix}-{yy}{str(num).zfill(4)}'
def gen_material_item_code(conn):
    yy = str(datetime.now().year)[(-2):]
    num = next_counter(conn, 'VT' + yy)
    return f'VT-{yy}{str(num).zfill(4)}'
def gen_maintenance_code(conn):
    yy = str(datetime.now().year)[(-2):]
    num = next_counter(conn, 'BT' + yy)
    return f'BT-{yy}{str(num).zfill(4)}'
def gen_borrow_code(conn):
    yy = str(datetime.now().year)[(-2):]
    num = next_counter(conn, 'MR' + yy)
    return f'MR-{yy}{str(num).zfill(4)}'
DEVICE_STATUS_OPTIONS = ['Bình thường', 'Cần bảo trì', 'Hỏng - chờ sửa', 'Đang mượn', 'Ngừng sử dụng', 'Chờ thanh lý', 'Đã thanh lý']
def _xlsx_col_letter(idx):
    """0-based column index -> tên cột kiểu Excel (0->A, 25->Z, 26->AA...)."""
    letters = ''
    idx += 1
    if idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    else:
        return letters
def _xlsx_escape(s):
    s = '' if s is None else str(s)
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\"', '&quot;')
def build_xlsx(sheets):
    """Tạo nội dung file .xlsx (bytes) gồm nhiều sheet.\nsheets: list các tuple (tên_sheet, [tiêu_đề...], [[giá_trị...], ...]).\nDòng tiêu đề (hàng 1) được tô đậm bằng style có sẵn trong styles.xml."""
    sheet_xmls = []
    sheet_entries = []
    for s_idx, (sheet_name, headers, rows) in enumerate(sheets, start=1):
        all_rows = [headers] + list(rows)
        row_xmls = []
        for r_idx, row in enumerate(all_rows, start=1):
            cells_xml = []
            for c_idx, val in enumerate(row):
                ref = f'{_xlsx_col_letter(c_idx)}{r_idx}'
                v = _xlsx_escape(val)
                style = ' s=\"1\"' if r_idx == 1 else ''
                cells_xml.append(f'<c r=\"{ref}\" t=\"inlineStr\"{style}><is><t xml:space=\"preserve\">{v}</t></is></c>')
            row_xmls.append(f'<row r=\"{r_idx}\">{''.join(cells_xml)}</row>')
        sheet_xmls.append(f'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>{''.join(row_xmls)}</sheetData></worksheet>')
        sheet_entries.append((f'sheet{s_idx}.xml', sheet_name[:31] or f'Sheet{s_idx}'))
    content_types_overrides = ''.join((f'<Override PartName=\"/xl/worksheets/{fn}\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>' for fn, _ in sheet_entries))
    content_types = f'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/><Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>{content_types_overrides}</Types>'
    root_rels = '<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>'
    sheets_tag = ''.join((f'<sheet name=\"{_xlsx_escape(disp)}\" sheetId=\"{i + 1}\" r:id=\"rId{i + 1}\"/>' for i, (fn, disp) in enumerate(sheet_entries)))
    workbook_xml = f'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets>{sheets_tag}</sheets></workbook>'
    workbook_rels_entries = ''.join((f'<Relationship Id=\"rId{i + 1}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/{fn}\"/>' for i, (fn, disp) in enumerate(sheet_entries)))
    workbook_rels = f'<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">{workbook_rels_entries}<Relationship Id=\"rId{len(sheet_entries) + 1}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/></Relationships>'
    styles_xml = '<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><fonts count=\"2\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font><font><b/><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts><fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill></fills><borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs><cellXfs count=\"2\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/><xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/></cellXfs><cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles></styleSheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('xl/workbook.xml', workbook_xml)
        z.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        z.writestr('xl/styles.xml', styles_xml)
        for fn, disp in sheet_entries:
            idx = sheet_entries.index((fn, disp))
            z.writestr(f'xl/worksheets/{fn}', sheet_xmls[idx])
    return buf.getvalue()
_XLSX_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
def parse_xlsx_first_sheet(file_bytes):
    """Đọc sheet đầu tiên của file .xlsx bằng thư viện chuẩn."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        names = z.namelist()
        sheets = sorted(n for n in names if n.startswith('xl/worksheets/') and n.endswith('.xml'))
        if not sheets:
            raise ValueError('Không tìm thấy dữ liệu sheet trong file Excel')
        shared = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall(f'{_XLSX_NS}si'):
                shared.append(''.join(t.text or '' for t in si.findall(f'.//{_XLSX_NS}t')))
        root = ET.fromstring(z.read(sheets[0]))
        rows_out = []
        for row_el in root.findall(f'.//{_XLSX_NS}sheetData/{_XLSX_NS}row'):
            vals = {}
            max_col = -1
            for c in row_el.findall(f'{_XLSX_NS}c'):
                ref = c.get('r') or ''
                letters = ''.join(ch for ch in ref if ch.isalpha())
                if not letters:
                    continue
                col = 0
                for ch in letters:
                    col = col * 26 + (ord(ch.upper()) - 64)
                col -= 1
                ctype = c.get('t')
                val = ''
                if ctype == 'inlineStr':
                    is_el = c.find(f'{_XLSX_NS}is')
                    if is_el is not None:
                        val = ''.join(t.text or '' for t in is_el.findall(f'.//{_XLSX_NS}t'))
                elif ctype == 's':
                    v = c.find(f'{_XLSX_NS}v')
                    if v is not None and v.text:
                        try:
                            val = shared[int(v.text)]
                        except Exception:
                            val = ''
                else:
                    v = c.find(f'{_XLSX_NS}v')
                    val = v.text or '' if v is not None else ''
                vals[col] = val
                max_col = max(max_col, col)
            rows_out.append([vals.get(i, '') for i in range(max_col + 1)])
        return rows_out
def _parse_number_cell(s):
    s = (s or '').strip()
    if not s:
        return 0.0
    else:
        s = s.replace(' ', '').replace(',', '')
        try:
            return float(s)
        except Exception:
            return 0.0
def device_to_dict(r):
    keys = set(r.keys())
    def g(k, default=''):
        return r[k] if k in keys and r[k] is not None else default
    return {
        'id': g('id'), 'assetCode': g('asset_code'), 'deptId': g('dept_id'),
        'posId': g('pos_id'), 'groupId': g('group_id'), 'typeId': g('type_id'),
        'model': g('model'), 'manufacturer': g('manufacturer'), 'serial': g('serial'),
        'config': g('config'), 'status': g('status'), 'importDate': g('import_date'),
        'allocateDate': g('allocate_date'), 'warrantyMonths': g('warranty_months'),
        'warrantyUnit': g('warranty_unit'), 'supplier': g('supplier'), 'value': g('value', 0),
        'note': g('note'), 'createdAt': g('created_at', 0),
        'computerName': g('computer_name'), 'mainboard': g('mainboard'), 'cpu': g('cpu'),
        'ram': g('ram'), 'gpu': g('gpu'), 'storage': g('storage'), 'osName': g('os_name'),
        'winKeyBios': g('win_key_bios'), 'winKeyCurrent': g('win_key_current'),
        'systemModel': g('system_model'), 'serialNumber': g('serial_number'),
        'macPhysical': g('mac_physical'), 'macVirtual': g('mac_virtual'), 'userId': g('user_id')
    }
def history_to_dict(r):
    try:
        device_ids = json.loads(r['device_ids']) if r['device_ids'] else []
    except Exception:
        device_ids = []
    return {'id': r['id'], 'code': r['code'], 'type': r['type'], 'date': r['date'], 'deviceIds': device_ids, 'deviceNames': r['device_names'] or '', 'newLocation': r['new_location'] or '', 'note': r['note'] or '', 'createdAt': r['created_at']}
def user_to_dict(r):
    return {'id': r['id'], 'fullName': r['full_name'] or '', 'email': r['email'] or '', 'phone': r['phone'] or '', 'deptId': r['dept_id'] or '', 'posId': r['pos_id'] or '', 'note': r['note'] or '', 'createdAt': r['created_at']}
def material_to_dict(r):
    return {'id': r['id'], 'code': r['code'] or '', 'name': r['name'] or '', 'category': r['category'] or '', 'minStock': r['min_stock'] or '', 'supplier': r['supplier'] or '', 'note': r['note'] or '', 'createdAt': r['created_at']}
def material_txn_to_dict(r):
    return {'id': r['id'], 'code': r['code'] or '', 'type': r['type'], 'materialId': r['material_id'], 'materialName': r['material_name'] or '', 'date': r['date'] or '', 'deptId': r['dept_id'] or '', 'posId': r['reason'] or '', 'performer': r['performer'] or '', 'note': r['note'] or '', 'createdAt': r['created_at']}
def maintenance_to_dict(r):
    return {'id': r['id'], 'code': r['code'] or '', 'deviceId': r['device_id'], 'type': r['type'] or 'suachua', 'issue': r['issue'] or 'dangxuly', 'startDate': r['start_date'] or '', 'expectedDate': r['performer'] or '', 'cost': r['cost'] or '', 'prevDeviceStatus': r['prev_device_status'] or '', 'createdAt': r['created_at']}
def branch_to_dict(r):
    return {'id': r['id'], 'name': r['name'] or '', 'address': r['address'] or '', 'phone': r['phone'] or '', 'note': r['note'] or '', 'createdAt': r['created_at']}
def borrow_to_dict(r):
    return {'id': r['id'], 'code': r['code'] or '', 'deviceId': r['device_id'], 'borrowerName': r['borrower_name'] or '', 'borrowerPhone': r['borrower_phone'] or '', 'status': r['status'] or '', 'dueDate': r['due_date'] or '', 'note': r['note'] or '', 'prevDeviceStatus': r['prev_device_status'] or '', 'createdAt': r['created_at']}
def material_stock(conn, material_id):
    row = conn.execute('SELECT\n        COALESCE(SUM(CASE WHEN type=\'nhap\' THEN quantity ELSE 0 END),0) -\n        COALESCE(SUM(CASE WHEN type=\'xuat\' THEN quantity ELSE 0 END),0) AS stock\n        FROM material_txn WHERE material_id=?', (material_id,)).fetchone()
    return row['stock'] or 0
def supplier_to_dict(r):
    return {'id': r['id'], 'name': r['name'] or '', 'taxCode': r['tax_code'] or '', 'email': r['email'] or '', 'hotline': r['hotline'] or '', 'website': r['website'] or '', 'contactPerson': r['contact_person'] or ''}
def account_to_dict(r):
    return {'id': r['id'], 'username': r['username'] or '', 'fullName': r['full_name'] or '', 'role': r['role'] or 'staff', 'status': r['status'] or 'active', 'note': r['note'] or '', 'createdAt': r['created_at'], 'lastLogin': r['last_login']}
def log_to_dict(r):
    return {'id': r['id'], 'module': r['module'] or '', 'action': r['action'] or '', 'detail': r['detail'] or '', 'actor': r['actor'] or 'Hệ thống', 'createdAt': r['created_at']}
def log_action(conn, module, action, detail, actor=None):
    """Ghi một dòng nhật ký hoạt động hệ thống. Không tự commit — hàm gọi\nchịu trách nhiệm conn.commit() (thường đã có sẵn ngay sau đó)."""
    conn.execute('INSERT INTO logs(id,module,action,detail,actor,created_at) VALUES(?,?,?,?,?,?)', (new_id(), module, action, detail, (actor or '').strip() or 'Hệ thống', now_ms()))
PERMISSION_MODULES = ['dashboard', 'devices', 'users', 'warehouse', 'allocation', 'maintenance', 'audit', 'reports', 'settings', 'accounts', 'permissions', 'logs']
PERMISSION_EDITABLE_ROLES = ['staff', 'viewer']
PERMISSION_LEVELS = {'view', 'none', 'edit'}
def default_permissions():
    staff_edit = {'warehouse', 'audit', 'maintenance', 'devices', 'allocation'}
    staff_view = {'dashboard', 'users', 'reports'}
    viewer_view = {'dashboard', 'reports', 'warehouse', 'devices', 'maintenance', 'audit', 'allocation', 'users'}
    staff = {}
    viewer = {}
    for mod in PERMISSION_MODULES:
        staff[mod] = 'edit' if mod in staff_edit else 'view' if mod in staff_view else 'none'
        viewer[mod] = 'view' if mod in viewer_view else 'none'
    return {'staff': staff, 'viewer': viewer}
def get_permissions(conn):
    row = conn.execute('SELECT value FROM settings WHERE [key]=\'permissions_matrix\'').fetchone()
    if not row or not row['value']:
        return default_permissions()
    else:
        try:
            data = json.loads(row['value'])
        except Exception:
            return default_permissions()
        defaults = default_permissions()
        result = {}
        for role in PERMISSION_EDITABLE_ROLES:
            role_data = data.get(role) if isinstance(data, dict) else None
            merged = {}
            for mod in PERMISSION_MODULES:
                v = role_data.get(mod) if isinstance(role_data, dict) else None
                merged[mod] = v if v in PERMISSION_LEVELS else defaults[role][mod]
            result[role] = merged
        return result
def get_bootstrap(conn, account=None):
    departments = []
    for d in conn.execute('SELECT * FROM departments ORDER BY name'):
        positions = [{'id': p['id'], 'name': p['name']} for p in conn.execute('SELECT * FROM positions WHERE dept_id=? ORDER BY name', (d['id'],))]
        dkeys = d.keys()
        departments.append({'id': d['id'], 'name': d['name'], 'branchId': d['branch_id'] if 'branch_id' in dkeys else '' or '', 'positions': positions})
    branches = [branch_to_dict(r) for r in conn.execute('SELECT * FROM branches ORDER BY name')]
    borrows = [borrow_to_dict(r) for r in conn.execute('SELECT * FROM borrows ORDER BY created_at DESC')]
    groups = []
    for g in conn.execute('SELECT * FROM groups_tbl ORDER BY name'):
        types = [{'id': t['id'], 'name': t['name']} for t in conn.execute('SELECT * FROM types_tbl WHERE group_id=? ORDER BY name', (g['id'],))]
        groups.append({'id': g['id'], 'name': g['name'], 'types': types})
    devices = [device_to_dict(r) for r in conn.execute('SELECT * FROM devices ORDER BY created_at DESC')]
    history = [history_to_dict(r) for r in conn.execute('SELECT * FROM history ORDER BY created_at DESC')]
    counters = {r['key']: r['value'] for r in conn.execute('SELECT * FROM counters')}
    settings = {r['key']: r['value'] for r in conn.execute('SELECT * FROM settings')}
    company = {'name': settings.get('company_name', ''), 'address': settings.get('company_address', ''), 'email': settings.get('company_email', ''), 'taxCode': settings.get('company_tax_code', '')}
    users = [user_to_dict(r) for r in conn.execute('SELECT * FROM users ORDER BY full_name')]
    suppliers = [supplier_to_dict(r) for r in conn.execute('SELECT * FROM suppliers ORDER BY name')]
    materials = [material_to_dict(r) for r in conn.execute('SELECT * FROM materials ORDER BY name')]
    material_txns = [material_txn_to_dict(r) for r in conn.execute('SELECT * FROM material_txn ORDER BY created_at DESC')]
    maintenance = [maintenance_to_dict(r) for r in conn.execute('SELECT * FROM maintenance ORDER BY created_at DESC')]
    if has_permission(conn, account, 'accounts', 'view'):
        accounts = [account_to_dict(r) for r in conn.execute('SELECT * FROM accounts ORDER BY created_at')]
    else:
        accounts = []
    permissions = get_permissions(conn)
    if has_permission(conn, account, 'logs', 'view'):
        logs = [log_to_dict(r) for r in conn.execute('SELECT * FROM logs ORDER BY created_at DESC LIMIT 500')]
    else:
        logs = []
    return {'departments': departments, 'groups': groups, 'devices': devices, 'history': history, 'counters': counters, 'company': company, 'users': users, 'suppliers': suppliers, 'materials': material_txns, 'accounts': accounts, 'permissions': permissions, 'logs': logs, 'maintenance': maintenance, 'branches': branches, 'borrows': borrows}
ROUTES = []
def route(method, pattern):
    regex = re.compile('^' + pattern + '$')
    def deco(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return deco
def err(msg, code=400):
    return (code, {'error': msg})
PERM_RANK = {'none': 0, 'view': 1, 'edit': 2}
def has_permission(conn, account, module, required='view'):
    """True nếu tài khoản hiện tại đủ quyền `required` (\'view\'/\'edit\') trên\n`module`. Tài khoản \'admin\' luôn có toàn quyền. Với \'staff\'/\'viewer\', tra\ntheo ma trận phân quyền do admin cấu hình (mặc định nếu chưa cấu hình)."""
    if not account:
        return False
    else:
        role = account.get('role') or 'staff'
        if role == 'admin':
            return True
        else:
            perms = get_permissions(conn)
            level = (perms.get(role) or {}).get(module, 'none')
            return PERM_RANK.get(level, 0) >= PERM_RANK.get(required, 1)
def guard(module, required='view'):
    def deco(fn):
        def wrapped(m, body, qs):
            conn = get_db()
            try:
                if not has_permission(conn, current_account(), module, required):
                    return err('Bạn không có quyền thực hiện thao tác này. Vui lòng liên hệ quản trị viên.', 403)
                return fn(m, body, qs)
            finally:
                conn.close()
        wrapped.__name__ = getattr(fn, '__name__', 'wrapped')
        return wrapped
    return deco
@route('POST', '/api/export/xlsx')
def r_export_xlsx(m, body, qs):
    body = body or {}
    module = (body.get('module') or '').strip()
    conn = get_db()
    try:
        if module and not has_permission(conn, current_account(), module, 'view'):
            return err('Bạn không có quyền xuất dữ liệu mục này.', 403)
        sheet_name = (body.get('sheetName') or 'Sheet1').strip() or 'Sheet1'
        columns = body.get('columns') or []
        rows_in = body.get('rows') or []
        filename = (body.get('filename') or 'xuat_du_lieu.xlsx').strip() or 'xuat_du_lieu.xlsx'
        if not isinstance(columns, list) or not columns:
            return err('Thiếu danh sách cột để xuất Excel')
        if not isinstance(rows_in, list):
            return err('Dữ liệu hàng không hợp lệ')
        data_rows = [[row.get(c, '') if isinstance(row, dict) else '' for c in columns] for row in rows_in]
        xlsx_bytes = build_xlsx([(sheet_name, columns, data_rows)])
        return (200, {'filename': filename, 'base64': base64.b64encode(xlsx_bytes).decode('ascii')})
    finally:
        conn.close()
def require_admin(fn):
    """Decorator: chỉ tài khoản vai trò \'admin\' mới được gọi route này — dùng\ncho các thao tác nhạy cảm không nên phụ thuộc vào ma trận phân quyền có\nthể chỉnh sửa được (đổi ma trận phân quyền, sao lưu/khôi phục toàn bộ dữ\nliệu), để tránh việc tự cấp quyền leo thang."""
    def wrapped(m, body, qs):
        account = current_account()
        if not account or (account.get('role') or 'staff') != 'admin':
            return err('Chỉ quản trị viên mới có quyền thực hiện thao tác này.', 403)
        else:
            return fn(m, body, qs)
    wrapped.__name__ = getattr(fn, '__name__', 'wrapped')
    return wrapped
def strip_deep(v):
    """Đệ quy .strip() mọi chuỗi trong dict/list lồng nhau trả về từ PowerShell JSON."""
    if isinstance(v, str):
        return v.strip()
    else:
        if isinstance(v, dict):
            return {k: strip_deep(x) for k, x in v.items()}
        else:
            if isinstance(v, list):
                return [strip_deep(x) for x in v]
            else:
                return v
def compute_windows_verdict(d):
    """Diễn giải kết quả quét sâu (chỉ đọc) thành mức độ cảnh báo + ghi chú khuyến nghị bằng tiếng Việt.\nKhông tự động kết luận \'100% là crack\' — chỉ nêu dấu hiệu để người dùng/IT tự xác minh."""
    findings = []
    severity = 'ok'
    kms_note = None
    if d.get('kmsServerMachine'):
        if d.get('kmsIsPrivateNetwork'):
            kms_note = f'Đang kích hoạt qua máy chủ KMS nội bộ ({d['kmsServerMachine']}) — hợp lệ (KMS doanh nghiệp).'
        else:
            kms_note = f'Đang kích hoạt qua máy chủ KMS có địa chỉ công cộng/không rõ nguồn gốc ({d['kmsServerMachine']}) — cần xác minh đây có phải KMS do công ty/nhà cung cấp quản lý hay không.'
            severity = 'warn'
        findings.append(kms_note)
    if d.get('masTraceFound'):
        findings.append('Phát hiện dấu vết lệnh tải/chạy script kích hoạt không rõ nguồn gốc (kiểu MAS/HWID) trong lịch sử PowerShell.')
        severity = 'danger'
    if d.get('kms38Suspected'):
        findings.append('Ngày hết hạn giấy phép hiển thị quanh năm 2038 — dấu hiệu đặc trưng của kỹ thuật giả lập KMS38.')
        severity = 'danger'
    if not d.get('hasBiosOemKey'):
        findings.append('Không có key OEM gắn trong BIOS — có thể là máy tự ráp/nâng cấp hợp pháp. Đề nghị kỹ thuật viên đối chiếu hoá đơn mua bản quyền trước khi kết luận, không nên quy kết vi phạm chỉ từ dấu hiệu này.')
        if severity == 'ok':
            severity = 'warn'
    folders = d.get('suspiciousFolders') or []
    if folders:
        findings.append(f'Tìm thấy {len(folders)} thư mục nghi vấn liên quan công cụ kích hoạt trái phép: {', '.join(folders)}.')
        severity = 'danger'
    tasks = d.get('suspiciousTasks') or []
    if tasks:
        findings.append(f'Tìm thấy {len(tasks)} tác vụ trong Task Scheduler nghi vấn tự động gia hạn kích hoạt trái phép: {', '.join(tasks)}.')
        severity = 'danger'
    if d.get('noGenTicketPolicy'):
        findings.append('Phát hiện chính sách registry \"NoGenTicket\" đang bật — đây là thủ thuật thường dùng để chặn Windows gửi xác thực bản quyền về Microsoft.')
        severity = 'danger'
    if not findings:
        findings.append('Không phát hiện dấu hiệu bất thường trong các mục đã quét.')
    label_map = {'ok': 'HỢP LỆ', 'warn': 'CẦN XÁC MINH THÊM', 'danger': 'NGHI VẤN VI PHẠM BẢN QUYỀN'}
    return {'severity': severity, 'label': label_map[severity], 'note': ' '.join(findings), 'findings': findings}
def run_powershell(script, timeout=25):
    """Chạy script PowerShell ở chế độ ẩn (không hiện cửa sổ console đen)."""
    cmd = ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', script]
    popen_kwargs = {}
    if os.name == 'nt':
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        popen_kwargs['startupinfo'] = si
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout, **popen_kwargs)
@route('GET', '/api/system-info')
@guard('devices', 'view')
def r_system_info(m, body, qs):
    info_type = (qs.get('type') or ['specs'])[0]
    try:
        if info_type == 'specs':
            ps_script = """
$ErrorActionPreference = 'SilentlyContinue'
$cs=Get-CimInstance Win32_ComputerSystem
$bios=Get-CimInstance Win32_BIOS
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu=Get-CimInstance Win32_VideoController | Select-Object -First 1
$board=Get-CimInstance Win32_BaseBoard
$disks=Get-CimInstance Win32_DiskDrive | ForEach-Object { "$($_.Model) ($([math]::Round($_.Size/1GB))GB)" }
$macPhys=Get-CimInstance Win32_NetworkAdapter -Filter "PhysicalAdapter=True AND MACAddress IS NOT NULL" | Select-Object -First 1 -ExpandProperty MACAddress
$result=[ordered]@{computerName=$env:COMPUTERNAME;mainboard=("$($board.Manufacturer) $($board.Product)").Trim();cpu=$cpu.Name;ram="$([math]::Round($cs.TotalPhysicalMemory/1GB)) GB";gpu=$gpu.Name;storage=($disks -join '; ');osName=$os.Caption;systemModel=$cs.Model;serialNumber=$bios.SerialNumber;macPhysical=$macPhys;model=$cs.Model;manufacturer=$cs.Manufacturer;serial=$bios.SerialNumber}
$result|ConvertTo-Json -Compress
"""
            data=json.loads(run_powershell(ps_script,25).strip() or '{}')
            return (200, {k:(v.strip() if isinstance(v,str) else (v or '')) for k,v in data.items()})
        if info_type == 'monitors':
            ps_script = """$ErrorActionPreference='SilentlyContinue';$results=@();$monitors=Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID;foreach($m in $monitors){$name=[Text.Encoding]::ASCII.GetString($m.UserFriendlyName).Trim([char]0);$mfg=[Text.Encoding]::ASCII.GetString($m.ManufacturerName).Trim([char]0);$ser=[Text.Encoding]::ASCII.GetString($m.SerialNumberID).Trim([char]0);$results+=[PSCustomObject]@{Name=$name;Manufacturer=$mfg;Serial=$ser}};$results|ConvertTo-Json -Compress"""
            out=run_powershell(ps_script,20).strip(); data=json.loads(out or '[]')
            if isinstance(data,dict): data=[data]
            return (200, {'monitors':[{'name':x.get('Name') or 'Màn hình','manufacturer':x.get('Manufacturer') or '','serial':x.get('Serial') or ''} for x in data]})
        if info_type == 'printers':
            ps_script="""$ErrorActionPreference='SilentlyContinue';$results=@();foreach($p in Get-CimInstance Win32_Printer){$results+=[PSCustomObject]@{Name=$p.Name;Manufacturer=$p.DriverName;Serial=$p.PortName;IsDefault=[bool]$p.Default}};$results|ConvertTo-Json -Compress"""
            out=run_powershell(ps_script,20).strip(); data=json.loads(out or '[]')
            if isinstance(data,dict): data=[data]
            return (200, {'printers':[{'name':x.get('Name') or 'Máy in','manufacturer':x.get('Manufacturer') or '','serial':x.get('Serial') or '','isDefault':bool(x.get('IsDefault'))} for x in data]})
        if info_type == 'license':
            ps_script="""
$ErrorActionPreference='SilentlyContinue';$os=Get-CimInstance Win32_OperatingSystem;$lic=Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL"|Select-Object -First 1
$map=@{0='Chưa kích hoạt';1='Đã kích hoạt (Licensed)';2='Bản dùng thử - Grace';3='Hết hạn - OOT Grace';4='Không xác định - Non-Genuine';5='Notification';6='Extended Grace'};$code=if($lic){[int]$lic.LicenseStatus}else{-1};$text=if($map.ContainsKey($code)){$map[$code]}else{'Không xác định'}
[ordered]@{computerName=$env:COMPUTERNAME;osName=$os.Caption;osVersion=$os.Version;edition=$lic.Name;partialKey=$lic.PartialProductKey;licenseStatusCode=$code;licenseStatus=$text}|ConvertTo-Json -Compress
"""
            return (200,json.loads(run_powershell(ps_script,20).strip() or '{}'))
        if info_type in ('windowsdeepscan','licensescan'):
            return (200, {'note':'Đã bỏ qua bước quét sâu trong bản server_fixed để đảm bảo tương thích Python 3.14. Các chức năng chính vẫn hoạt động.'})
        return err('Loại yêu cầu không hợp lệ')
    except Exception as e:
        return (500, {'error': f'Lỗi đọc thông tin hệ thống: {e}'})
@route('GET', '/api/bootstrap')
def r_bootstrap(m, body, qs):
    conn = get_db()
    data = get_bootstrap(conn, current_account())
    conn.close()
    return (200, data)
@route('POST', '/api/settings/company')
@guard('settings', 'edit')
def r_settings_company(m, body, qs):
    fields = {'company_name': (body.get('name') or '').strip(), 'company_address': (body.get('address') or '').strip(), 'company_email': (body.get('email') or '').strip(), 'company_tax_code': (body.get('taxCode') or '').strip()}
    conn = get_db()
    for k, v in fields.items():
        upsert_kv(conn, 'settings', k, v)
    log_action(conn, 'Cài đặt', 'Cập nhật', f'Cập nhật thông tin công ty: {fields['company_name'] or '(chưa đặt tên)'}')
    conn.commit()
    conn.close()
    return (200, {'name': fields['company_name'], 'address': fields['company_address'], 'email': fields['company_email'], 'taxCode': fields['company_tax_code']})
@route('POST', '/api/settings/permissions')
@require_admin
def r_settings_permissions(m, body, qs):
    if not isinstance(body, dict):
        return err('Dữ liệu phân quyền không hợp lệ')
    else:
        cleaned = {}
        for role in PERMISSION_EDITABLE_ROLES:
            role_data = body.get(role)
            if not isinstance(role_data, dict):
                return err('Dữ liệu phân quyền không hợp lệ')
            else:
                cleaned[role] = {}
                for mod in PERMISSION_MODULES:
                    v = role_data.get(mod, 'none')
                    if v not in PERMISSION_LEVELS:
                        return err(f'Giá trị quyền không hợp lệ cho mục \"{mod}\"')
                    else:
                        cleaned[role][mod] = v
        conn = get_db()
        upsert_kv(conn, 'settings', 'permissions_matrix', json.dumps(cleaned))
        log_action(conn, 'Phân quyền', 'Cập nhật', 'Cập nhật ma trận phân quyền theo vai trò')
        conn.commit()
        result = get_permissions(conn)
        conn.close()
        return (200, result)
def _dept_pos_pairs(conn):
    pairs = []
    for d in conn.execute('SELECT * FROM departments ORDER BY name'):
        poss = conn.execute('SELECT name FROM positions WHERE dept_id=? ORDER BY name', (d['id'],)).fetchall()
        if poss:
            pairs += [(d['name'], p['name']) for p in poss]
        else:
            pairs.append((d['name'], ''))
    return pairs
def _group_type_pairs(conn):
    pairs = []
    for g in conn.execute('SELECT * FROM groups_tbl ORDER BY name'):
        typs = conn.execute('SELECT name FROM types_tbl WHERE group_id=? ORDER BY name', (g['id'],)).fetchall()
        if typs:
            pairs += [(g['name'], t['name']) for t in typs]
        else:
            pairs.append((g['name'], ''))
    return pairs
@route('GET', '/api/devices/import-template')
@guard('devices', 'edit')
def r_devices_import_template(m, body, qs):
    conn = get_db()
    try:
        dept0 = conn.execute('SELECT * FROM departments ORDER BY name').fetchone()
        pos0 = conn.execute('SELECT * FROM positions WHERE dept_id=? ORDER BY name', (dept0['id'],)).fetchone() if dept0 else None
        grp0 = conn.execute('SELECT * FROM groups_tbl ORDER BY name').fetchone()
        typ0 = conn.execute('SELECT * FROM types_tbl WHERE group_id=? ORDER BY name', (grp0['id'],)).fetchone() if grp0 else None
        headers = ['Mã tài sản (để trống để tự sinh)', 'Phòng/Ban *', 'Vị trí cụ thể *', 'Nhóm thiết bị *', 'Loại thiết bị *', 'Model', 'Hãng sản xuất', 'Số Serial', 'Cấu hình/Mô tả', 'Tình trạng', 'Ngày nhập kho (YYYY-MM-DD)', 'Ngày phân bổ (YYYY-MM-DD)', 'Số tháng/năm bảo hành', 'Đơn vị bảo hành (Tháng/Năm)', 'Nhà cung cấp', 'Giá trị (VNĐ)', 'Người sử dụng (họ tên)', 'Ghi chú']
        example = ['', dept0['name'] if dept0 else 'Phòng Kế toán', pos0['name'] if pos0 else 'Phòng Kế toán', typ0['name'] if typ0 else 'Laptop', 'Dell Latitude 5420', 'Dell', 'SN123456', 'Core i5/8GB/256GB SSD', 'Bình thường', datetime.now().strftime('%Y-%m-%d'), '', '24', 'Tháng', 'Công ty ABC', '15000000', '', '']
        dept_pos_pairs = _dept_pos_pairs(conn)
        group_type_pairs = _group_type_pairs(conn)
        user_names = [r['full_name'] for r in conn.execute('SELECT full_name FROM users ORDER BY full_name')]
        ref_headers = ['Phòng/Ban', 'Vị trí cụ thể', 'Nhóm thiết bị', 'Loại thiết bị', 'Người dùng hiện có', 'Tình trạng hợp lệ']
        ref_rows = []
        for dp, gt, un, st in zip_longest(dept_pos_pairs, group_type_pairs, user_names, DEVICE_STATUS_OPTIONS, fillvalue=None):
            ref_rows.append([dp[0] if dp else '', dp[1] if dp else '', gt[0] if gt else '', gt[1] if gt else '', un or '', st or ''])
        xlsx_bytes = build_xlsx([('Nhập thiết bị', headers, [example]), ('Danh mục tham khảo', ref_headers, ref_rows)])
    finally:
        conn.close()
    return (200, {'filename': 'mau_nhap_thiet_bi.xlsx', 'base64': base64.b64encode(xlsx_bytes).decode('ascii')})
@route('POST', '/api/devices/import')
@guard('devices', 'edit')
def r_devices_import(m, body, qs):
    b64=(body or {}).get('fileBase64') or ''
    if not b64: return err('Vui lòng chọn file Excel để nhập')
    try: rows=parse_xlsx_first_sheet(base64.b64decode(b64))
    except Exception as e: return err(f'Không đọc được file Excel: {e}')
    if len(rows)<2: return err('File Excel không có dữ liệu')
    conn=get_db(); success=0; errors=[]
    try:
        depts={(d['name'] or '').strip().lower():d for d in conn.execute('SELECT * FROM departments')}
        pos={}
        for r in conn.execute('SELECT * FROM positions'): pos[(r['dept_id'],(r['name'] or '').strip().lower())]=r
        groups={(r['name'] or '').strip().lower():r for r in conn.execute('SELECT * FROM groups_tbl')}
        types={}
        for r in conn.execute('SELECT * FROM types_tbl'): types[(r['group_id'],(r['name'] or '').strip().lower())]=r
        existing={(r['serial'] or '').strip().lower() for r in conn.execute('SELECT serial FROM devices') if r['serial']}
        headers=rows[0]
        idx={str(v).strip():i for i,v in enumerate(headers)}
        def cell(row, name, fallback=None):
            i=idx.get(name, fallback if fallback is not None else -1)
            return str(row[i]).strip() if 0<=i<len(row) and row[i] is not None else ''
        for n,row in enumerate(rows[1:],2):
            dept=cell(row,'Phòng/Ban *',1); position=cell(row,'Vị trí cụ thể *',2); group=cell(row,'Nhóm thiết bị *',3); typ=cell(row,'Loại thiết bị *',4)
            if not all([dept,position,group,typ]): errors.append(f'Dòng {n}: thiếu thông tin bắt buộc'); continue
            d=depts.get(dept.lower()); g=groups.get(group.lower())
            if not d: errors.append(f'Dòng {n}: không tìm thấy Phòng/Ban "{dept}"'); continue
            if not g: errors.append(f'Dòng {n}: không tìm thấy Nhóm thiết bị "{group}"'); continue
            ppos=pos.get((d['id'],position.lower())); t=types.get((g['id'],typ.lower()))
            if not ppos: errors.append(f'Dòng {n}: không tìm thấy Vị trí "{position}"'); continue
            if not t: errors.append(f'Dòng {n}: không tìm thấy Loại thiết bị "{typ}"'); continue
            serial=cell(row,'Số Serial',7)
            if serial and serial.lower() in existing: errors.append(f'Dòng {n}: Số Serial "{serial}" đã tồn tại'); continue
            code=cell(row,'Mã tài sản (để trống để tự sinh)',0) or gen_asset_code(conn,typ)
            conn.execute('INSERT INTO devices(id,asset_code,dept_id,pos_id,group_id,type_id,model,manufacturer,serial,config,status,import_date,allocate_date,warranty_months,warranty_unit,supplier,value,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(new_id(),code,d['id'],ppos['id'],g['id'],t['id'],cell(row,'Model',5),cell(row,'Hãng sản xuất',6),serial,cell(row,'Cấu hình/Mô tả',8),cell(row,'Tình trạng',9) or 'Bình thường',cell(row,'Ngày nhập kho (YYYY-MM-DD)',10),cell(row,'Ngày phân bổ (YYYY-MM-DD)',11),cell(row,'Số tháng/năm bảo hành',12),cell(row,'Đơn vị bảo hành (Tháng/Năm)',13),cell(row,'Nhà cung cấp',14),_parse_number_cell(cell(row,'Giá trị (VNĐ)',15)),cell(row,'Ghi chú',17),now_ms()))
            if serial: existing.add(serial.lower())
            success+=1
        conn.commit()
    except Exception as e:
        conn.rollback(); return err(f'Lỗi nhập dữ liệu: {e}',500)
    finally: conn.close()
    return (200,{'success':success,'errors':errors})
@route('POST', '/api/devices')
@guard('devices', 'edit')
def r_devices_create(m, body, qs):
    for f in ['deptId','posId','groupId','typeId']:
        if not body.get(f): return err(f'Thiếu trường bắt buộc: {f}')
    serial_val=(body.get('serial') or '').strip(); conn=get_db()
    try:
        if serial_val and conn.execute('SELECT 1 FROM devices WHERE LOWER(TRIM(serial))=LOWER(?)',(serial_val,)).fetchone(): return err(f'Số Serial "{serial_val}" đã tồn tại!')
        trow=conn.execute('SELECT name FROM types_tbl WHERE id=?',(body['typeId'],)).fetchone(); asset_code=(body.get('assetCode') or '').strip() or gen_asset_code(conn,trow['name'] if trow else 'TB'); did=new_id()
        vals=(did,asset_code,body['deptId'],body['posId'],body['groupId'],body['typeId'],body.get('model',''),body.get('manufacturer',''),serial_val,body.get('config',''),body.get('status','Bình thường'),body.get('importDate',''),body.get('allocateDate',''),body.get('warrantyMonths',''),body.get('warrantyUnit','Tháng'),body.get('supplier',''),_parse_number_cell(str(body.get('value',''))),body.get('note',''),now_ms(),body.get('computerName',''),body.get('mainboard',''),body.get('cpu',''),body.get('ram',''),body.get('gpu',''),body.get('storage',''),body.get('osName',''),body.get('winKeyBios',''),body.get('winKeyCurrent',''),body.get('systemModel',''),body.get('serialNumber',''),body.get('macPhysical',''),body.get('macVirtual',''),body.get('userId',''))
        conn.execute('INSERT INTO devices(id,asset_code,dept_id,pos_id,group_id,type_id,model,manufacturer,serial,config,status,import_date,allocate_date,warranty_months,warranty_unit,supplier,value,note,created_at,computer_name,mainboard,cpu,ram,gpu,storage,os_name,win_key_bios,win_key_current,system_model,serial_number,mac_physical,mac_virtual,user_id) VALUES('+','.join('?' for _ in vals)+')',vals)
        log_action(conn,'Thiết bị','Thêm mới',f"Thêm thiết bị {asset_code}"); conn.commit(); r=conn.execute('SELECT * FROM devices WHERE id=?',(did,)).fetchone(); return (200,device_to_dict(r))
    except Exception as e: conn.rollback(); return err(f'Lỗi thêm thiết bị: {e}',500)
    finally: conn.close()
@route('PUT', '/api/devices/([a-zA-Z0-9]+)')
@guard('devices', 'edit')
def r_devices_update(m, body, qs):
    did=m.group(1); conn=get_db()
    try:
        serial_val=(body.get('serial') or '').strip()
        if serial_val and conn.execute('SELECT 1 FROM devices WHERE id!=? AND LOWER(TRIM(serial))=LOWER(?)',(did,serial_val)).fetchone(): return err(f'Số Serial "{serial_val}" đã tồn tại trên thiết bị khác!')
        fields=['dept_id','pos_id','group_id','type_id','model','manufacturer','serial','config','status','import_date','allocate_date','warranty_months','warranty_unit','supplier','value','note','computer_name','mainboard','cpu','ram','gpu','storage','os_name','win_key_bios','win_key_current','system_model','serial_number','mac_physical','mac_virtual','user_id']
        vals=[body.get({'dept_id':'deptId','pos_id':'posId','group_id':'groupId','type_id':'typeId'}.get(f,f), '') for f in fields]
        vals[14]=_parse_number_cell(str(body.get('value',''))); vals.append(did)
        conn.execute('UPDATE devices SET '+','.join(f+'=?' for f in fields)+' WHERE id=?',vals); conn.commit(); r=conn.execute('SELECT * FROM devices WHERE id=?',(did,)).fetchone(); return (200,device_to_dict(r)) if r else err('Không tìm thấy thiết bị',404)
    except Exception as e: conn.rollback(); return err(f'Lỗi cập nhật thiết bị: {e}',500)
    finally: conn.close()
@route('POST', '/api/devices/delete')
@guard('devices', 'edit')
def r_devices_delete(m, body, qs):
    ids=body.get('ids',[])
    if not ids: return err('Không có ID để xóa')
    conn=get_db()
    try:
        q=','.join('?' for _ in ids); conn.execute(f'DELETE FROM devices WHERE id IN ({q})',ids); conn.commit(); return (200,{'ok':True})
    except Exception as e: conn.rollback(); return err(f'Lỗi xóa thiết bị: {e}',500)
    finally: conn.close()
@route('POST', '/api/devices/handover')
@guard('allocation', 'edit')
def r_devices_handover(m, body, qs):
    device_ids = body.get('deviceIds', [])
    if not device_ids:
        return err('Vui lòng chọn thiết bị')
    else:
        conn = get_db()
        devs = conn.execute(f'SELECT * FROM devices WHERE id IN ({','.join(('?' for _ in device_ids))})', device_ids).fetchall()
        if not devs:
            conn.close()
            return err('Không tìm thấy thiết bị')
        else:
            user_id = (body.get('userId') or '').strip()
            user_row = None
            if user_id:
                user_row = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
                if not user_row:
                    conn.close()
                    return err('Không tìm thấy người dùng đã chọn')
            dnames = ', '.join((f'{d['asset_code']} ({d['model'] or ''})' for d in devs))
            old_loc = f'{devs[0]['dept_id']}/{devs[0]['pos_id']}'
            new_dept = body.get('newDeptId') or (user_row['dept_id'] if user_row else None) or devs[0]['dept_id']
            new_pos = body.get('newPosId') or (user_row['pos_id'] if user_row else None) or devs[0]['pos_id']
            loc_changed = new_dept != devs[0]['dept_id'] or new_pos != devs[0]['pos_id']
            if loc_changed or user_id:
                set_cols = ['dept_id=?', 'pos_id=?']
                set_vals = [new_dept, new_pos]
                if user_id:
                    set_cols.append('user_id=?')
                    set_vals.append(user_id)
                conn.execute(f'UPDATE devices SET {', '.join(set_cols)} WHERE id IN ({','.join(('?' for _ in device_ids))})', set_vals + device_ids)
            code = gen_doc_code(conn, 'handover')
            conn.execute('INSERT INTO history(id,code,type,date,device_ids,device_names,old_location,new_location,performer,note,created_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?)', (new_id(), code, 'handover', body.get('date', ''), json.dumps(device_ids), dnames, f'{new_dept}/{new_pos}', f'{body.get('from', '')} -> {body.get('to', '')}', body.get('note', ''), now_ms()))
            log_action(conn, 'Thiết bị', 'Bàn giao', f'Bàn giao {len(device_ids)} thiết bị ({dnames})' + (f' cho {user_row['full_name']}' if user_row else ''), actor=f'{body.get('from', '')} -> {body.get('to', '')}')
            conn.commit()
            conn.close()
            return (200, {'ok': True})
@route('POST', '/api/devices/transfer')
@guard('allocation', 'edit')
def r_devices_transfer(m, body, qs):
    device_ids = body.get('deviceIds', [])
    dept_id = body.get('deptId')
    pos_id = body.get('posId')
    if not device_ids or not dept_id or (not pos_id):
        return err('Thiếu thông tin điều chuyển')
    else:
        conn = get_db()
        devs = conn.execute(f'SELECT * FROM devices WHERE id IN ({','.join(('?' for _ in device_ids))})', device_ids).fetchall()
        if not devs:
            conn.close()
            return err('Không tìm thấy thiết bị')
        else:
            dnames = ', '.join((f'{d['asset_code']} ({d['model'] or ''})' for d in devs))
            old_loc = f'{devs[0]['dept_id']}/{devs[0]['pos_id']}'
            conn.execute(f'UPDATE devices SET dept_id=?, pos_id=? WHERE id IN ({','.join(('?' for _ in device_ids))})', [dept_id, pos_id] + device_ids)
            code = gen_doc_code(conn, 'transfer')
            conn.execute('INSERT INTO history(id,code,type,date,device_ids,device_names,old_location,new_location,performer,note,created_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?)', (new_id(), code, 'transfer', body.get('date', ''), json.dumps(device_ids), dnames, old_loc, f'{dept_id}/{pos_id}', body.get('performer', ''), body.get('note', ''), now_ms()))
            log_action(conn, 'Thiết bị', 'Điều chuyển', f'Điều chuyển {len(device_ids)} thiết bị ({dnames})', actor=body.get('performer', ''))
            conn.commit()
            conn.close()
            return (200, {'ok': True})
@route('POST', '/api/devices/reclaim')
@guard('allocation', 'edit')
def r_devices_reclaim(m, body, qs):
    device_ids = body.get('deviceIds', [])
    if not device_ids:
        return err('Vui lòng chọn thiết bị')
    else:
        performer = (body.get('performer') or '').strip()
        if not performer:
            return err('Vui lòng nhập người thực hiện thu hồi')
        else:
            conn = get_db()
            devs = conn.execute(f'SELECT * FROM devices WHERE id IN ({','.join(('?' for _ in device_ids))})', device_ids).fetchall()
            if not devs:
                conn.close()
                return err('Không tìm thấy thiết bị')
            else:
                wh_dept_id = ensure_warehouse_location(conn)
                wh_pos = conn.execute('SELECT id FROM positions WHERE dept_id=? AND name=\'Kho\'', (wh_dept_id,)).fetchone()
                wh_pos_id = wh_pos['id'] if wh_pos else ''
                dnames = ', '.join((f'{d['asset_code']} ({d['model'] or ''})' for d in devs))
                old_loc = f'{devs[0]['dept_id']}/{devs[0]['pos_id']}'
                conn.execute(f'UPDATE devices SET dept_id=?, pos_id=?, user_id=? WHERE id IN ({','.join(('?' for _ in device_ids))})', [wh_dept_id, wh_pos_id, ''] + device_ids)
                code = gen_doc_code(conn, 'reclaim')
                conn.execute('INSERT INTO history(id,code,type,date,device_ids,device_names,old_location,new_location,performer,note,created_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?)', (new_id(), code, 'reclaim', body.get('date', ''), json.dumps(device_ids), dnames, old_loc, f'{wh_dept_id}/{wh_pos_id}', performer, body.get('note', ''), now_ms()))
                log_action(conn, 'Thiết bị', 'Thu hồi', f'Thu hồi {len(device_ids)} thiết bị về kho ({dnames})', actor=performer)
                conn.commit()
                conn.close()
                return (200, {'ok': True})
@route('POST', '/api/history/delete')
@guard('allocation', 'edit')
def r_history_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        conn.execute(f'DELETE FROM history WHERE id IN ({','.join(('?' for _ in ids))})', ids)
        log_action(conn, 'Lịch sử cấp phát', 'Xóa', f'Xóa {len(ids)} bản ghi lịch sử cấp phát/điều chuyển')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('POST', '/api/history/clear')
@guard('allocation', 'edit')
def r_history_clear(m, body, qs):
    conn = get_db()
    conn.execute('DELETE FROM history')
    log_action(conn, 'Lịch sử cấp phát', 'Xóa', 'Xóa toàn bộ lịch sử cấp phát/điều chuyển')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/logs/delete')
@guard('logs', 'edit')
def r_logs_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        conn.execute(f'DELETE FROM logs WHERE id IN ({','.join(('?' for _ in ids))})', ids)
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('POST', '/api/logs/clear')
@guard('logs', 'edit')
def r_logs_clear(m, body, qs):
    conn = get_db()
    conn.execute('DELETE FROM logs')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('GET', '/api/stats')
@guard('reports', 'view')
def r_stats(m, body, qs):
    group_id = (qs.get('groupId') or [''])[0]
    conn = get_db()
    where = 'WHERE group_id=?' if group_id else ''
    params = [group_id] if group_id else []
    total = conn.execute(f'SELECT COUNT(*) c FROM devices {where}', params).fetchone()['c']
    dept_rows = []
    for d in conn.execute('SELECT * FROM departments ORDER BY name'):
        positions = []
        dept_total = 0
        for p in conn.execute('SELECT * FROM positions WHERE dept_id=? ORDER BY name', (d['id'],)):
            pwhere = 'WHERE dept_id=? AND pos_id=?' + (' AND group_id=?' if group_id else '')
            pparams = [d['id'], p['id']] + ([group_id] if group_id else [])
            cnt = conn.execute(f'SELECT COUNT(*) c FROM devices {pwhere}', pparams).fetchone()['c']
            positions.append({'id': p['id'], 'name': p['name'], 'count': cnt})
            dept_total += cnt
        dept_rows.append({'id': d['id'], 'name': d['name'], 'count': dept_total, 'positions': positions})
    conn.close()
    return (200, {'total': total, 'departments': dept_rows})
@route('POST', '/api/groups')
@guard('settings', 'edit')
def r_groups_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên nhóm không được để trống')
    else:
        gid = new_id()
        conn = get_db()
        conn.execute('INSERT INTO groups_tbl(id,name) VALUES(?,?)', (gid, name))
        log_action(conn, 'Danh mục', 'Thêm mới', f'Thêm nhóm thiết bị \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'id': gid, 'name': name, 'types': []})
@route('PUT', '/api/groups/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_groups_update(m, body, qs):
    gid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên nhóm không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE groups_tbl SET name=? WHERE id=?', (name, gid))
        log_action(conn, 'Danh mục', 'Cập nhật', f'Cập nhật nhóm thiết bị \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('DELETE', '/api/groups/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_groups_delete(m, body, qs):
    gid = m.group(1)
    conn = get_db()
    grow = conn.execute('SELECT name FROM groups_tbl WHERE id=?', (gid,)).fetchone()
    conn.execute('DELETE FROM types_tbl WHERE group_id=?', (gid,))
    conn.execute('DELETE FROM groups_tbl WHERE id=?', (gid,))
    log_action(conn, 'Danh mục', 'Xóa', f'Xóa nhóm thiết bị \"{(grow['name'] if grow else gid)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/groups/([a-zA-Z0-9]+)/types')
@guard('settings', 'edit')
def r_types_create(m, body, qs):
    gid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên loại không được để trống')
    else:
        tid = new_id()
        conn = get_db()
        conn.execute('INSERT INTO types_tbl(id,group_id,name) VALUES(?,?,?)', (tid, gid, name))
        log_action(conn, 'Danh mục', 'Thêm mới', f'Thêm loại thiết bị \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'id': tid, 'groupId': gid, 'name': name})
@route('PUT', '/api/types/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_types_update(m, body, qs):
    tid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên loại không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE types_tbl SET name=? WHERE id=?', (name, tid))
        log_action(conn, 'Danh mục', 'Cập nhật', f'Cập nhật loại thiết bị \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('DELETE', '/api/types/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_types_delete(m, body, qs):
    tid = m.group(1)
    conn = get_db()
    trow = conn.execute('SELECT name FROM types_tbl WHERE id=?', (tid,)).fetchone()
    conn.execute('DELETE FROM types_tbl WHERE id=?', (tid,))
    log_action(conn, 'Danh mục', 'Xóa', f'Xóa loại thiết bị \"{(trow['name'] if trow else tid)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/departments')
@guard('settings', 'edit')
def r_depts_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên phòng ban không được để trống')
    else:
        branch_id = (body.get('branchId') or '').strip() or None
        did = new_id()
        conn = get_db()
        conn.execute('INSERT INTO departments(id,name,branch_id) VALUES(?,?,?)', (did, name, branch_id))
        log_action(conn, 'Phòng/Ban', 'Thêm mới', f'Thêm phòng/ban \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'id': did, 'name': name, 'branchId': branch_id or '', 'positions': []})
@route('PUT', '/api/departments/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_depts_update(m, body, qs):
    did = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên phòng ban không được để trống')
    else:
        conn = get_db()
        if 'branchId' in body:
            branch_id = (body.get('branchId') or '').strip() or None
            conn.execute('UPDATE departments SET name=?,branch_id=? WHERE id=?', (name, branch_id, did))
        else:
            conn.execute('UPDATE departments SET name=? WHERE id=?', (name, did))
        log_action(conn, 'Phòng/Ban', 'Cập nhật', f'Cập nhật phòng/ban \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('DELETE', '/api/departments/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_depts_delete(m, body, qs):
    did = m.group(1)
    conn = get_db()
    drow = conn.execute('SELECT name FROM departments WHERE id=?', (did,)).fetchone()
    conn.execute('DELETE FROM positions WHERE dept_id=?', (did,))
    conn.execute('DELETE FROM departments WHERE id=?', (did,))
    log_action(conn, 'Phòng/Ban', 'Xóa', f'Xóa phòng/ban \"{(drow['name'] if drow else did)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/departments/([a-zA-Z0-9]+)/positions')
@guard('settings', 'edit')
def r_pos_create(m, body, qs):
    did = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên vị trí không được để trống')
    else:
        pid = new_id()
        conn = get_db()
        conn.execute('INSERT INTO positions(id,dept_id,name) VALUES(?,?,?)', (pid, did, name))
        log_action(conn, 'Phòng/Ban', 'Thêm mới', f'Thêm vị trí \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'id': pid, 'deptId': did, 'name': name})
@route('PUT', '/api/positions/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_pos_update(m, body, qs):
    pid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên vị trí không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE positions SET name=? WHERE id=?', (name, pid))
        log_action(conn, 'Phòng/Ban', 'Cập nhật', f'Cập nhật vị trí \"{name}\"')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('DELETE', '/api/positions/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_pos_delete(m, body, qs):
    pid = m.group(1)
    conn = get_db()
    prow = conn.execute('SELECT name FROM positions WHERE id=?', (pid,)).fetchone()
    conn.execute('DELETE FROM positions WHERE id=?', (pid,))
    log_action(conn, 'Phòng/Ban', 'Xóa', f'Xóa vị trí \"{(prow['name'] if prow else pid)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/suppliers')
@guard('settings', 'edit')
def r_suppliers_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên nhà cung cấp không được để trống')
    else:
        sid = new_id()
        conn = get_db()
        conn.execute('INSERT INTO suppliers(id,name,tax_code,email,hotline,website,contact_person) VALUES(?,?,?,?,?,?,?)', (sid, name, (body.get('taxCode') or '').strip(), (body.get('email') or '').strip(), (body.get('hotline') or '').strip(), (body.get('website') or '').strip()))
        log_action(conn, 'Nhà cung cấp', 'Thêm mới', f'Thêm nhà cung cấp \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM suppliers WHERE id=?', (sid,)).fetchone()
        conn.close()
        return (200, supplier_to_dict(r))
@route('PUT', '/api/suppliers/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_suppliers_update(m, body, qs):
    sid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên nhà cung cấp không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE suppliers SET name=?,tax_code=?,email=?,hotline=?,website=?,contact_person=? WHERE id=?', (name, (body.get('taxCode') or '').strip(), (body.get('email') or '').strip(), (body.get('hotline') or '').strip(), (body.get('website') or '').strip(), sid))
        log_action(conn, 'Nhà cung cấp', 'Cập nhật', f'Cập nhật nhà cung cấp \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM suppliers WHERE id=?', (sid,)).fetchone()
        conn.close()
        if not r:
            return err('Không tìm thấy nhà cung cấp', 404)
        else:
            return (200, supplier_to_dict(r))
@route('DELETE', '/api/suppliers/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_suppliers_delete(m, body, qs):
    sid = m.group(1)
    conn = get_db()
    srow = conn.execute('SELECT name FROM suppliers WHERE id=?', (sid,)).fetchone()
    conn.execute('DELETE FROM suppliers WHERE id=?', (sid,))
    log_action(conn, 'Nhà cung cấp', 'Xóa', f'Xóa nhà cung cấp \"{(srow['name'] if srow else sid)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/branches')
@guard('settings', 'edit')
def r_branches_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên chi nhánh không được để trống')
    else:
        bid = new_id()
        conn = get_db()
        conn.execute('INSERT INTO branches(id,name,address,phone,note,created_at) VALUES(?,?,?,?,?,?)', (bid, name, (body.get('address') or '').strip(), (body.get('phone') or '').strip(), (body.get('note') or '').strip(), now_ms()))
        log_action(conn, 'Chi nhánh', 'Thêm mới', f'Thêm chi nhánh \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM branches WHERE id=?', (bid,)).fetchone()
        conn.close()
        return (200, branch_to_dict(r))
@route('PUT', '/api/branches/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_branches_update(m, body, qs):
    bid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên chi nhánh không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE branches SET name=?,address=?,phone=?,note=? WHERE id=?', (name, (body.get('address') or '').strip(), (body.get('phone') or '').strip(), (body.get('note') or '').strip(), bid))
        log_action(conn, 'Chi nhánh', 'Cập nhật', f'Cập nhật chi nhánh \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM branches WHERE id=?', (bid,)).fetchone()
        conn.close()
        if not r:
            return err('Không tìm thấy chi nhánh', 404)
        else:
            return (200, branch_to_dict(r))
@route('DELETE', '/api/branches/([a-zA-Z0-9]+)')
@guard('settings', 'edit')
def r_branches_delete(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    brow = conn.execute('SELECT name FROM branches WHERE id=?', (bid,)).fetchone()
    conn.execute('UPDATE departments SET branch_id=NULL WHERE branch_id=?', (bid,))
    conn.execute('DELETE FROM branches WHERE id=?', (bid,))
    log_action(conn, 'Chi nhánh', 'Xóa', f'Xóa chi nhánh \"{(brow['name'] if brow else bid)}\"')
    conn.commit()
    conn.close()
    return (200, {'ok': True})
ACCOUNT_ROLES = {'admin', 'viewer', 'staff'}
ACCOUNT_STATUSES = {'active', 'locked'}
@route('POST', '/api/accounts')
@guard('accounts', 'edit')
def r_accounts_create(m, body, qs):
    username = (body.get('username') or '').strip().lower()
    password = body.get('password') or ''
    full_name = (body.get('fullName') or '').strip()
    role = (body.get('role') or 'staff').strip()
    status = (body.get('status') or 'active').strip()
    if not username:
        return err('Tên đăng nhập không được để trống')
    else:
        if not re.match('^[a-z0-9._-]{3,32}$', username):
            return err('Tên đăng nhập chỉ gồm chữ thường, số, dấu chấm/gạch, 3-32 ký tự')
        else:
            if not full_name:
                return err('Họ tên không được để trống')
            else:
                if len(password) < 4:
                    return err('Mật khẩu phải có ít nhất 4 ký tự')
                else:
                    if role not in ACCOUNT_ROLES:
                        return err('Vai trò không hợp lệ')
                    else:
                        if status not in ACCOUNT_STATUSES:
                            return err('Trạng thái không hợp lệ')
                        else:
                            acting_role = (current_account() or {}).get('role') or 'staff'
                            if role == 'admin' and acting_role != 'admin':
                                return err('Chỉ quản trị viên mới có thể tạo tài khoản với vai trò Quản trị viên', 403)
                            else:
                                conn = get_db()
                                exists = conn.execute('SELECT id FROM accounts WHERE username=?', (username,)).fetchone()
                                if exists:
                                    conn.close()
                                    return err('Tên đăng nhập đã tồn tại')
                                else:
                                    aid = new_id()
                                    conn.execute('INSERT INTO accounts(id,username,password_hash,full_name,role,status,note,created_at,last_login) VALUES(?,?,?,?,?,?,?,?,?)', (aid, username, hash_password(password), full_name, role, status, (body.get('note') or '').strip(), now_ms(), None))
                                    log_action(conn, 'Tài khoản', 'Thêm mới', f'Thêm tài khoản \"{username}\" ({full_name}) — vai trò {role}')
                                    conn.commit()
                                    r = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
                                    conn.close()
                                    return (200, account_to_dict(r))
@route('PUT', '/api/accounts/([a-zA-Z0-9]+)')
@guard('accounts', 'edit')
def r_accounts_update(m, body, qs):
    aid = m.group(1)
    full_name = (body.get('fullName') or '').strip()
    role = (body.get('role') or 'staff').strip()
    status = (body.get('status') or 'active').strip()
    password = body.get('password') or ''
    if not full_name:
        return err('Họ tên không được để trống')
    else:
        if role not in ACCOUNT_ROLES:
            return err('Vai trò không hợp lệ')
        else:
            if status not in ACCOUNT_STATUSES:
                return err('Trạng thái không hợp lệ')
            else:
                if password and len(password) < 4:
                    return err('Mật khẩu phải có ít nhất 4 ký tự')
                else:
                    acting_role = (current_account() or {}).get('role') or 'staff'
                    conn = get_db()
                    row = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
                    if not row:
                        conn.close()
                        return err('Không tìm thấy tài khoản', 404)
                    else:
                        target_role = row['role'] or 'staff'
                        if (target_role == 'admin' or role == 'admin') and acting_role != 'admin':
                            conn.close()
                            return err('Chỉ quản trị viên mới có thể thay đổi tài khoản Quản trị viên', 403)
                        else:
                            if target_role == 'admin' and role != 'admin':
                                other_admins = conn.execute('SELECT COUNT(*) c FROM accounts WHERE role=\'admin\' AND status=\'active\' AND id<>?', (aid,)).fetchone()['c']
                                if other_admins == 0:
                                    conn.close()
                                    return err('Không thể hạ quyền quản trị viên cuối cùng của hệ thống. Hãy tạo một tài khoản Quản trị viên khác trước.')
                            if status != 'active':
                                others_active = conn.execute('SELECT COUNT(*) c FROM accounts WHERE status=\'active\' AND id<>?', (aid,)).fetchone()['c']
                                if others_active == 0 and row['status'] == 'active':
                                    conn.close()
                                    return err('Không thể khóa tài khoản quản trị đang hoạt động cuối cùng')
                            if password:
                                conn.execute('UPDATE accounts SET full_name=?,role=?,status=?,note=?,password_hash=? WHERE id=?', (full_name, role, status, (body.get('note') or '').strip(), hash_password(password), aid))
                            else:
                                conn.execute('UPDATE accounts SET full_name=?,role=?,status=?,note=? WHERE id=?', (full_name, role, status, (body.get('note') or '').strip(), aid))
                            if status != row['status']:
                                log_action(conn, 'Tài khoản', 'Khóa' if status != 'active' else 'Mở khóa', f'{('Khóa' if status != 'active' else 'Mở khóa')} tài khoản \"{row['username']}\"')
                            else:
                                detail = f'Cập nhật tài khoản \"{row['username']}\"'
                                if password:
                                    detail += ' (đổi mật khẩu)'
                                log_action(conn, 'Tài khoản', 'Cập nhật', detail)
                            conn.commit()
                            r = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
                            conn.close()
                            return (200, account_to_dict(r))
@route('DELETE', '/api/accounts/([a-zA-Z0-9]+)')
@guard('accounts', 'edit')
def r_accounts_delete(m, body, qs):
    aid = m.group(1)
    acting_role = (current_account() or {}).get('role') or 'staff'
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) c FROM accounts').fetchone()['c']
    if total <= 1:
        conn.close()
        return err('Không thể xóa tài khoản cuối cùng của hệ thống')
    else:
        arow = conn.execute('SELECT username, role FROM accounts WHERE id=?', (aid,)).fetchone()
        target_role = arow['role'] or 'staff' if arow else 'staff'
        if target_role == 'admin' and acting_role != 'admin':
            conn.close()
            return err('Chỉ quản trị viên mới có thể xóa tài khoản Quản trị viên', 403)
        else:
            if arow and target_role == 'admin':
                other_admins = conn.execute('SELECT COUNT(*) c FROM accounts WHERE role=\'admin\' AND status=\'active\' AND id<>?', (aid,)).fetchone()['c']
                if other_admins == 0:
                    conn.close()
                    return err('Không thể xóa quản trị viên cuối cùng của hệ thống. Hãy tạo một tài khoản Quản trị viên khác trước.')
            conn.execute('DELETE FROM accounts WHERE id=?', (aid,))
            log_action(conn, 'Tài khoản', 'Xóa', f'Xóa tài khoản \"{(arow['username'] if arow else aid)}\"')
            conn.commit()
            conn.close()
            return (200, {'ok': True})
@route('POST', '/api/login')
def r_login(m, body, qs):
    body = body or {}
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    if not username or not password:
        return err('Vui lòng nhập tên đăng nhập và mật khẩu')
    else:
        conn = get_db()
        row = conn.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
        if not row or not verify_password(password, row['password_hash']):
            conn.close()
            return err('Tên đăng nhập hoặc mật khẩu không đúng', 401)
        else:
            if (row['status'] or 'active') != 'active':
                conn.close()
                return err('Tài khoản này đã bị khóa. Vui lòng liên hệ quản trị viên.', 403)
            else:
                token = new_token()
                account = account_to_dict(row)
                SESSIONS[token] = {'accountId': account['id'], 'username': account['username'], 'fullName': account['fullName'], 'role': account['role']}
                conn.execute('UPDATE accounts SET last_login=? WHERE id=?', (now_ms(), account['id']))
                log_action(conn, 'Đăng nhập', 'Đăng nhập', f'Tài khoản \"{username}\" đã đăng nhập', actor=account['fullName'] or username)
                conn.commit()
                row2 = conn.execute('SELECT * FROM accounts WHERE id=?', (account['id'],)).fetchone()
                account = account_to_dict(row2)
                conn.close()
                return (200, {'token': token, 'account': account})
@route('POST', '/api/logout')
def r_logout(m, body, qs):
    token = getattr(_local, 'token', None)
    if token and token in SESSIONS:
            del SESSIONS[token]
    return (200, {'ok': True})
@route('POST', '/api/materials')
@guard('warehouse', 'edit')
def r_materials_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên vật tư không được để trống')
    else:
        conn = get_db()
        code = (body.get('code') or '').strip() or gen_material_item_code(conn)
        mid = new_id()
        conn.execute('INSERT INTO materials(id,code,name,category,unit,min_stock,supplier,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (mid, code, name, (body.get('category') or '').strip(), (body.get('unit') or '').strip(), float(body.get('minStock') or '').strip(), (body.get('note') or '').strip(), now_ms()))
        log_action(conn, 'Kho vật tư', 'Thêm mới', f'Thêm vật tư \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM materials WHERE id=?', (mid,)).fetchone()
        conn.close()
        return (200, material_to_dict(r))
@route('PUT', '/api/materials/([a-zA-Z0-9]+)')
@guard('warehouse', 'edit')
def r_materials_update(m, body, qs):
    mid = m.group(1)
    name = (body.get('name') or '').strip()
    if not name:
        return err('Tên vật tư không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE materials SET code=?,name=?,category=?,unit=?,min_stock=?,supplier=?,note=? WHERE id=?', ((body.get('code') or '').strip(), name, (body.get('category') or '').strip(), (body.get('unit') or '').strip(), float(body.get('minStock') or '').strip(), (body.get('note') or '').strip(), mid))
        log_action(conn, 'Kho vật tư', 'Cập nhật', f'Cập nhật vật tư \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM materials WHERE id=?', (mid,)).fetchone()
        conn.close()
        if not r:
            return err('Không tìm thấy vật tư', 404)
        else:
            return (200, material_to_dict(r))
@route('POST', '/api/materials/delete')
@guard('warehouse', 'edit')
def r_materials_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        q = ','.join(('?' for _ in ids))
        names = [r['name'] for r in conn.execute(f'SELECT name FROM materials WHERE id IN ({q})', ids)]
        conn.execute(f'DELETE FROM materials WHERE id IN ({q})', ids)
        log_action(conn, 'Kho vật tư', 'Xóa', f'Xóa {len(ids)} vật tư: {(', '.join(names) if names else '')}')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('POST', '/api/material-txn')
@guard('warehouse', 'edit')
def r_material_txn_create(m, body, qs):
    ttype = body.get('type')
    if ttype not in ['nhap', 'xuat']:
        return err('Loại phiếu không hợp lệ')
    else:
        material_id = body.get('materialId')
        if not material_id:
            return err('Vui lòng chọn vật tư')
        else:
            try:
                qty = float(body.get('quantity') or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                return err('Số lượng phải lớn hơn 0')
            else:
                conn = get_db()
                mrow = conn.execute('SELECT * FROM materials WHERE id=?', (material_id,)).fetchone()
                if not mrow:
                    conn.close()
                    return err('Không tìm thấy vật tư')
                else:
                    if ttype == 'xuat':
                        stock = material_stock(conn, material_id)
                        if qty > stock:
                            conn.close()
                            return err(f'Số lượng xuất ({qty}) vượt quá tồn kho hiện có ({stock}) của \"{mrow['name']}\"')
                    tid = new_id()
                    code = gen_material_code(conn, ttype)
                    conn.execute('INSERT INTO material_txn(id,code,type,material_id,material_name,quantity,unit,date,\n        dept_id,pos_id,supplier,reason,performer,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (tid, code, ttype, material_id, mrow['name'], qty, (body.get('unit') or mrow['unit'] or body.get('date', '')).strip(), (body.get('deptId', '') or body.get('posId', '')).strip(), (body.get('supplier') or body.get('reason')).strip(), (body.get('performer') or body.get('note')).strip(), now_ms()))
                    log_action(conn, f'Kho vật tư{('Nhập kho' if ttype == 'nhap' else 'Xuất kho')} {qty} {(body.get('unit') or mrow['unit']).strip()} \"{mrow['name']}\"', actor=(body.get('performer') or '').strip())
                    conn.commit()
                    r = conn.execute('SELECT * FROM material_txn WHERE id=?', (tid,)).fetchone()
                    conn.close()
                    return (200, material_txn_to_dict(r))
@route('POST', '/api/material-txn/delete')
@guard('warehouse', 'edit')
def r_material_txn_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        q = ','.join(('?' for _ in ids))
        conn.execute(f'DELETE FROM material_txn WHERE id IN ({q})', ids)
        log_action(conn, 'Kho vật tư', 'Xóa', f'Xóa {len(ids)} phiếu nhập/xuất kho')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
STATUS_OPTIONS_PY = ['Bình thường', 'Cần bảo trì', 'Hỏng - chờ sửa', 'Đang mượn', 'Ngừng sử dụng', 'Chờ thanh lý', 'Đã thanh lý']
MAINTENANCE_DEVICE_STATUS = {'baotri': 'Cần bảo trì', 'suachua': 'Hỏng - chờ sửa'}
@route('POST', '/api/maintenance')
@guard('maintenance', 'edit')
def r_maintenance_create(m, body, qs):
    device_id = body.get('deviceId')
    if not device_id:
        return err('Vui lòng chọn thiết bị')
    else:
        mtype = body.get('type') or 'suachua'
        if mtype not in MAINTENANCE_DEVICE_STATUS:
            return err('Loại phiếu không hợp lệ')
        else:
            issue = (body.get('issue') or '').strip()
            if not issue:
                return err('Vui lòng nhập nội dung / mô tả sự cố')
            else:
                conn = get_db()
                dev = conn.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
                if not dev:
                    conn.close()
                    return err('Không tìm thấy thiết bị')
                else:
                    mid = new_id()
                    code = gen_maintenance_code(conn)
                    prev_status = dev['status'] or 'Bình thường'
                    new_status = MAINTENANCE_DEVICE_STATUS[mtype]
                    conn.execute('INSERT INTO maintenance(id,code,device_id,type,issue,status,start_date,expected_date,\n        complete_date,performer,cost,note,prev_device_status,created_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (mid, code, device_id, mtype, issue, 'dangxuly', (body.get('startDate') or '').strip(), body.get('expectedDate') or '', '', (body.get('performer') or '').strip(), 0, body.get('note') or '', prev_status, now_ms()))
                    conn.execute('UPDATE devices SET status=? WHERE id=?', (new_status, device_id))
                    log_action(conn, 'Bảo trì & Sửa chữa', 'Tạo phiếu', f'Tạo phiếu {('bảo trì' if mtype == 'baotri' else 'sửa chữa')} \"{code}\" cho thiết bị {dev['asset_code']}', actor=(body.get('performer') or '').strip())
                    conn.commit()
                    r = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
                    conn.close()
                    return (200, maintenance_to_dict(r))
@route('PUT', '/api/maintenance/([a-zA-Z0-9]+)')
@guard('maintenance', 'edit')
def r_maintenance_update(m, body, qs):
    mid = m.group(1)
    conn = get_db()
    row = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
    if not row:
        conn.close()
        return err('Không tìm thấy phiếu bảo trì / sửa chữa', 404)
    else:
        if row['status'] != 'dangxuly':
            conn.close()
            return err('Chỉ có thể sửa phiếu đang trong trạng thái xử lý')
        else:
            issue = (body.get('issue') or '').strip()
            if not issue:
                conn.close()
                return err('Vui lòng nhập nội dung / mô tả sự cố')
            else:
                mtype = body.get('type') or row['type']
                if mtype not in MAINTENANCE_DEVICE_STATUS:
                    conn.close()
                    return err('Loại phiếu không hợp lệ')
                else:
                    conn.execute('UPDATE maintenance SET type=?,issue=?,expected_date=?,performer=?,note=? WHERE id=?', (mtype, issue, (body.get('expectedDate') or '').strip(), (body.get('performer') or '').strip(), (body.get('note') or '').strip(), mid))
                    conn.execute('UPDATE devices SET status=? WHERE id=?', (MAINTENANCE_DEVICE_STATUS[mtype], row['device_id']))
                    log_action(conn, 'Bảo trì & Sửa chữa', 'Cập nhật', f'Cập nhật phiếu \"{row['code']}\"')
                    conn.commit()
                    r = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
                    conn.close()
                    return (200, maintenance_to_dict(r))
@route('POST', '/api/maintenance/([a-zA-Z0-9]+)/complete')
@guard('maintenance', 'edit')
def r_maintenance_complete(m, body, qs):
    mid = m.group(1)
    conn = get_db()
    row = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
    if not row:
        conn.close()
        return err('Không tìm thấy phiếu bảo trì / sửa chữa', 404)
    else:
        if row['status'] != 'dangxuly':
            conn.close()
            return err('Phiếu này đã được xử lý xong')
        else:
            try:
                cost = float(body.get('cost') or 0)
            except (TypeError, ValueError):
                cost = 0
            if cost < 0:
                cost = 0
            device_status = body.get('deviceStatus') or 'Bình thường'
            if device_status not in STATUS_OPTIONS_PY:
                device_status = 'Bình thường'
            complete_date = (body.get('completeDate') or '').strip() or datetime.now().strftime('%Y-%m-%d')
            note = row['note'] or ''
            extra_note = (body.get('note') or '').strip()
            if extra_note:
                note = (note + ' | ' if note else '') + extra_note
            conn.execute('UPDATE maintenance SET status=?,complete_date=?,cost=?,note=? WHERE id=?', ('hoanthanh', complete_date, cost, note, mid))
            conn.execute('UPDATE devices SET status=? WHERE id=?', (device_status, row['device_id']))
            dev = conn.execute('SELECT asset_code FROM devices WHERE id=?', (row['device_id'],)).fetchone()
            log_action(conn, 'Bảo trì & Sửa chữa', 'Hoàn thành', f'Hoàn thành phiếu \"{row['code']}\" cho thiết bị {(dev['asset_code'] if dev else '')} — chi phí {cost:,.0f}đ'.replace(',', '.'))
            conn.commit()
            r = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
            conn.close()
            return (200, maintenance_to_dict(r))
@route('POST', '/api/maintenance/([a-zA-Z0-9]+)/cancel')
@guard('maintenance', 'edit')
def r_maintenance_cancel(m, body, qs):
    mid = m.group(1)
    conn = get_db()
    row = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
    if not row:
        conn.close()
        return err('Không tìm thấy phiếu bảo trì / sửa chữa', 404)
    else:
        if row['status'] != 'dangxuly':
            conn.close()
            return err('Phiếu này đã được xử lý xong')
        else:
            conn.execute('UPDATE maintenance SET status=? WHERE id=?', ('huy', mid))
            conn.execute('UPDATE devices SET status=? WHERE id=?', (row['prev_device_status'] or 'Bình thường', row['device_id']))
            log_action(conn, 'Bảo trì & Sửa chữa', 'Hủy phiếu', f'Hủy phiếu \"{row['code']}\"')
            conn.commit()
            r = conn.execute('SELECT * FROM maintenance WHERE id=?', (mid,)).fetchone()
            conn.close()
            return (200, maintenance_to_dict(r))
@route('POST', '/api/maintenance/delete')
@guard('maintenance', 'edit')
def r_maintenance_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        q = ','.join(('?' for _ in ids))
        rows = conn.execute(f'SELECT * FROM maintenance WHERE id IN ({q})', ids).fetchall()
        for row in rows:
            if row['status'] == 'dangxuly':
                conn.execute('UPDATE devices SET status=? WHERE id=?', (row['prev_device_status'] or 'Bình thường', row['device_id']))
        conn.execute(f'DELETE FROM maintenance WHERE id IN ({q})', ids)
        log_action(conn, 'Bảo trì & Sửa chữa', 'Xóa', f'Xóa {len(ids)} phiếu bảo trì/sửa chữa')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
BORROW_STATUS_LABELS = {'dangmuon': 'Đang mượn', 'datra': 'Đã trả'}
@route('POST', '/api/borrows')
@guard('allocation', 'edit')
def r_borrows_create(m, body, qs):
    device_id = body.get('deviceId')
    if not device_id:
        return err('Vui lòng chọn thiết bị')
    else:
        borrower_name = (body.get('borrowerName') or '').strip()
        if not borrower_name:
            return err('Vui lòng nhập tên người mượn')
        else:
            conn = get_db()
            dev = conn.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
            if not dev:
                conn.close()
                return err('Không tìm thấy thiết bị')
            else:
                existing = conn.execute('SELECT id FROM borrows WHERE device_id=? AND status=\'dangmuon\'', (device_id,)).fetchone()
                if existing:
                    conn.close()
                    return err('Thiết bị này đang trong một phiếu mượn khác, chưa được trả')
                else:
                    bid = new_id()
                    code = gen_borrow_code(conn)
                    prev_status = dev['status'] or 'Bình thường'
                    conn.execute('INSERT INTO borrows(id,code,device_id,borrower_name,borrower_dept_id,borrower_pos_id,\n        borrower_phone,status,borrow_date,due_date,return_date,performer,note,prev_device_status,created_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (bid, code, device_id, borrower_name, (body.get('borrowerDeptId') or '').strip(), (body.get('borrowerPosId') or '').strip(), (body.get('borrowerPhone') or '').strip(), (body.get('performer') or '').strip(), (body.get('note') or '').strip(), prev_status, now_ms()))
                    conn.execute('UPDATE devices SET status=? WHERE id=?', ('Đang mượn', device_id))
                    log_action(conn, 'Mượn/Trả', 'Tạo phiếu mượn', f'Tạo phiếu mượn \"{code}\" cho thiết bị {dev['asset_code']} — người mượn: {borrower_name}', actor=(body.get('performer') or '').strip())
                    conn.commit()
                    r = conn.execute('SELECT * FROM borrows WHERE id=?', (bid,)).fetchone()
                    conn.close()
                    return (200, borrow_to_dict(r))
@route('POST', '/api/borrows/([a-zA-Z0-9]+)/return')
@guard('allocation', 'edit')
def r_borrows_return(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    row = conn.execute('SELECT * FROM borrows WHERE id=?', (bid,)).fetchone()
    if not row:
        conn.close()
        return err('Không tìm thấy phiếu mượn', 404)
    else:
        if row['status'] != 'dangmuon':
            conn.close()
            return err('Phiếu này đã được xử lý (trả) trước đó')
        else:
            device_status = body.get('deviceStatus') or row['prev_device_status'] or 'Bình thường'
            if device_status not in STATUS_OPTIONS_PY:
                device_status = row['prev_device_status'] or 'Bình thường'
            return_date = (body.get('returnDate') or '').strip() or datetime.now().strftime('%Y-%m-%d')
            note = row['note'] or ''
            extra_note = (body.get('note') or '').strip()
            if extra_note:
                note = (note + ' | ' if note else '') + extra_note
            conn.execute('UPDATE borrows SET status=?,return_date=?,note=? WHERE id=?', ('datra', return_date, note, bid))
            conn.execute('UPDATE devices SET status=? WHERE id=?', (device_status, row['device_id']))
            dev = conn.execute('SELECT asset_code FROM devices WHERE id=?', (row['device_id'],)).fetchone()
            log_action(conn, 'Mượn/Trả', 'Trả thiết bị', f'Trả thiết bị {(dev['asset_code'] if dev else '')} — phiếu \"{row['code']}\"')
            conn.commit()
            r = conn.execute('SELECT * FROM borrows WHERE id=?', (bid,)).fetchone()
            conn.close()
            return (200, borrow_to_dict(r))
@route('POST', '/api/borrows/delete')
@guard('allocation', 'edit')
def r_borrows_delete(m, body, qs):
    ids = body.get('ids', [])
    if not ids:
        return err('Không có ID để xóa')
    else:
        conn = get_db()
        q = ','.join(('?' for _ in ids))
        rows = conn.execute(f'SELECT * FROM borrows WHERE id IN ({q})', ids).fetchall()
        for row in rows:
            if row['status'] == 'dangmuon':
                conn.execute('UPDATE devices SET status=? WHERE id=?', (row['prev_device_status'] or 'Bình thường', row['device_id']))
        conn.execute(f'DELETE FROM borrows WHERE id IN ({q})', ids)
        log_action(conn, 'Mượn/Trả', 'Xóa', f'Xóa {len(ids)} phiếu mượn/trả')
        conn.commit()
        conn.close()
        return (200, {'ok': True})
@route('GET', '/api/backup')
@require_admin
def r_backup(m, body, qs):
    conn = get_db()
    data = get_bootstrap(conn)
    log_action(conn, 'Sao lưu', 'Tải xuống', 'Tải xuống bản sao lưu toàn bộ dữ liệu hệ thống (JSON)')
    conn.commit()
    conn.close()
    return (200, data)
AUDIT_SCAN_STATUS_LABEL = {'pending': 'Chưa kiểm kê', 'matched': 'Đã kiểm kê (khớp)', 'mismatch': 'Đã kiểm kê (lệch)', 'not_found': 'Không tìm thấy', 'extra': 'Phát hiện thừa'}
def audit_batch_to_dict(r, counts=None):
    d = {'id': r['id'], 'code': r['code'] or '', 'name': r['name'] or 'all', 'scopeDeptId': r['scope_dept_id'] or '', 'scopeGroupId': r['scope_group_id'] or 'active', 'dueDate': r['due_date'] or '', 'createdBy': r['created_by'] or '', 'createdAt': r['created_at'], 'completedAt': r['completed_at']}
    if counts is not None:
        d['counts'] = counts
    return d
def audit_item_to_dict(r):
    keys=set(r.keys())
    def g(k,d=''): return r[k] if k in keys and r[k] is not None else d
    return {'id':g('id'),'batchId':g('batch_id'),'deviceId':g('device_id'),'assetCode':g('asset_code'),'model':g('snap_model'),'manufacturer':g('snap_manufacturer'),'serial':g('snap_serial'),'deptId':g('snap_dept_id'),'deptName':g('snap_dept_name'),'posId':g('snap_pos_id'),'posName':g('snap_pos_name'),'groupName':g('snap_group_name'),'typeName':g('snap_type_name'),'status':g('snap_status'),'userName':g('snap_user_name'),'itemType':g('item_type'),'scanStatus':g('scan_status','pending'),'locationMatch':g('location_match'),'conditionMatch':g('condition_match'),'actualDeptId':g('actual_dept_id'),'actualPosId':g('actual_pos_id'),'actualStatus':g('actual_status'),'scannedBy':g('scanned_by'),'scannedAt':g('scanned_at',0),'note':g('note')}
def _audit_batch_counts(conn, batch_id):
    rows = conn.execute('SELECT scan_status, COUNT(*) c FROM audit_items WHERE batch_id=? GROUP BY scan_status', (batch_id,)).fetchall()
    counts = {k: 0 for k in AUDIT_SCAN_STATUS_LABEL}
    total = 0
    for r in rows:
        counts[r['scan_status'] or 'pending'] = r['c']
        total += r['c']
    counts['total'] = total
    counts['plannedTotal'] = total - counts.get('extra', 0)
    return counts
@route('GET', '/api/audit/batches')
@guard('audit', 'view')
def r_audit_batches_list(m, body, qs):
    conn = get_db()
    rows = conn.execute('SELECT * FROM audit_batches ORDER BY created_at DESC').fetchall()
    items = [audit_batch_to_dict(r, counts=_audit_batch_counts(conn, r['id'])) for r in rows]
    conn.close()
    return (200, {'items': items})
@route('POST', '/api/audit/batches')
@guard('audit', 'edit')
def r_audit_batches_create(m, body, qs):
    name = (body.get('name') or '').strip()
    if not name:
        return err('Vui lòng nhập tên đợt kiểm kê')
    else:
        scope_type = body.get('scopeType') or 'all'
        scope_dept_id = body.get('scopeDeptId') or ''
        scope_group_id = body.get('scopeGroupId') or ''
        if scope_type == 'department' and (not scope_dept_id):
            return err('Vui lòng chọn phòng/ban cho phạm vi kiểm kê')
        else:
            if scope_type == 'group' and (not scope_group_id):
                return err('Vui lòng chọn nhóm thiết bị cho phạm vi kiểm kê')
            else:
                conn = get_db()
                q = 'SELECT * FROM devices WHERE 1=1'
                params = []
                if scope_type == 'department':
                    q += ' AND dept_id=?'
                else:
                    if scope_type == 'group':
                        q += ' AND group_id=?'
                devices = conn.execute(q, params).fetchall()
                if not devices:
                    conn.close()
                    return err('Không có thiết bị nào trong phạm vi đã chọn')
                else:
                    account = current_account()
                    actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
                    bid = new_id()
                    code = gen_audit_batch_code(conn)
                    conn.execute('INSERT INTO audit_batches(id,code,name,scope_type,scope_dept_id,scope_group_id,\n        status,due_date,note,created_by,created_at,completed_at)\n        VALUES(?,?,?,?,?,?,\'active\',?,?,?,?,NULL)', (bid, code, name, scope_type, scope_dept_id, scope_group_id, body.get('dueDate', ''), body.get('note', ''), actor, now_ms()))
                    def lookup(table, col, val):
                        if not val:
                            return ''
                        else:
                            r = conn.execute(f'SELECT name FROM {table} WHERE {col}=?', (val,)).fetchone()
                            return r['name'] if r else ''
                    for dev in devices:
                        d = device_to_dict(dev)
                        usr = conn.execute('SELECT full_name FROM users WHERE id=?', (d.get('userId', ''),)).fetchone() if d.get('userId') else None
                        conn.execute('INSERT INTO audit_items(id,batch_id,device_id,asset_code,snap_model,snap_manufacturer,\n            snap_serial,snap_dept_id,snap_dept_name,snap_pos_id,snap_pos_name,snap_group_name,snap_type_name,\n            snap_status,snap_user_name,item_type,scan_status,location_match,condition_match,\n            actual_dept_id,actual_pos_id,actual_status,scanned_by,scanned_at,note)\n            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,\'planned\',\'pending\',NULL,NULL,\'\',\'\',\'\', \'\', NULL, \'\')', (new_id(), bid, d['id'], d.get('assetCode', ''), d.get('model', ''), d.get('manufacturer', ''), d.get('serial', ''), lookup('departments', 'id', d.get('posId', '')), lookup('groups_tbl', 'id', d.get('groupId', '')), d.get('status', ''), usr['full_name'] if usr else ''))
                    log_action(conn, 'Kiểm kê', 'Tạo đợt', f'Tạo đợt kiểm kê \"{name}\" ({code}) — {len(devices)} thiết bị', actor=actor)
                    conn.commit()
                    row = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
                    result = audit_batch_to_dict(row, counts=_audit_batch_counts(conn, bid))
                    conn.close()
                    return (200, result)
@route('GET', '/api/audit/batches/([a-zA-Z0-9]+)')
@guard('audit', 'view')
def r_audit_batch_detail(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
    if not brow:
        conn.close()
        return err('Không tìm thấy đợt kiểm kê', 404)
    else:
        items = conn.execute('SELECT * FROM audit_items WHERE batch_id=? ORDER BY snap_dept_name, snap_pos_name, asset_code', (bid,)).fetchall()
        result = audit_batch_to_dict(brow, counts=_audit_batch_counts(conn, bid))
        result['items'] = [audit_item_to_dict(r) for r in items]
        conn.close()
        return (200, result)
@route('POST', '/api/audit/batches/([a-zA-Z0-9]+)/scan')
@guard('audit', 'edit')
def r_audit_scan(m, body, qs):
    bid = m.group(1)
    raw = (body.get('code') or '').strip()
    if not raw:
        return err('Vui lòng nhập hoặc quét mã tài sản')
    else:
        asset_code = raw
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith('MA TAI SAN:'):
                asset_code = line.split(':', 1)[1].strip()
                break
        cur_dept_id = body.get('currentDeptId') or ''
        cur_pos_id = body.get('currentPosId') or ''
        conn = get_db()
        brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
        if not brow:
            conn.close()
            return err('Không tìm thấy đợt kiểm kê', 404)
        else:
            if brow['status'] != 'active':
                conn.close()
                return err('Đợt kiểm kê này đã đóng hoặc bị hủy, không thể tiếp tục quét')
            else:
                account = current_account()
                actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
                item = conn.execute('SELECT * FROM audit_items WHERE batch_id=? AND asset_code=?', (bid, asset_code)).fetchone()
                dev = conn.execute('SELECT * FROM devices WHERE asset_code=?', (asset_code,)).fetchone()
                if not item and (not dev):
                    conn.close()
                    return err(f'Không tìm thấy thiết bị với mã \"{asset_code}\" trong hệ thống', 404)
                else:
                    def lookup(table, col, val):
                        if not val:
                            return ''
                        else:
                            r = conn.execute(f'SELECT name FROM {table} WHERE {col}=?', (val,)).fetchone()
                            return r['name'] if r else ''
                    if not item and dev:
                        d = device_to_dict(dev)
                        usr = conn.execute('SELECT full_name FROM users WHERE id=?', (d.get('userId', ''),)).fetchone() if d.get('userId') else None
                        loc_match = 'match' if cur_dept_id == d.get('deptId', '') and cur_pos_id == d.get('posId', '') else 'mismatch'
                        iid = new_id()
                        conn.execute('INSERT INTO audit_items(id,batch_id,device_id,asset_code,snap_model,snap_manufacturer,\n            snap_serial,snap_dept_id,snap_dept_name,snap_pos_id,snap_pos_name,snap_group_name,snap_type_name,\n            snap_status,snap_user_name,item_type,scan_status,location_match,condition_match,\n            actual_dept_id,actual_pos_id,actual_status,scanned_by,scanned_at,note)\n            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,\'extra\',\'extra\',?,\'match\',?,?,?,?,?,?)', (iid, bid, d['id'], asset_code, d.get('model', ''), d.get('manufacturer', ''), d.get('serial', ''), lookup('departments', 'id', d.get('deptId', '')), lookup('groups_tbl', 'id', d.get('groupId', '')), loc_match, cur_dept_id, cur_pos_id, d.get('status', ''), actor, now_ms(), 'Ngoài phạm vi đợt kiểm kê'))
                        log_action(conn, 'Kiểm kê', 'Quét (thừa)', f'Phát hiện thừa \"{asset_code}\" ngoài phạm vi đợt \"{brow['name']}\"', actor=actor)
                        conn.commit()
                        result_item = conn.execute('SELECT * FROM audit_items WHERE id=?', (iid,)).fetchone()
                        conn.close()
                        return (200, {'result': 'extra', 'item': audit_item_to_dict(result_item)})
                    else:
                        loc_match = 'match' if cur_dept_id == (item['snap_dept_id'] or '') and cur_pos_id == (item['snap_pos_id'] or '') else 'mismatch'
                        cond_match = 'match'
                        scan_status = 'matched' if loc_match == 'match' else 'mismatch'
                        conn.execute('UPDATE audit_items SET scan_status=?, location_match=?, condition_match=?,\n        actual_dept_id=?, actual_pos_id=?, actual_status=?, scanned_by=?, scanned_at=? WHERE id=?', (scan_status, loc_match, cond_match, cur_dept_id, cur_pos_id, item['snap_status'] or '', actor, now_ms(), item['id']))
                        log_action(conn, 'Kiểm kê', 'Quét', f'Quét \"{asset_code}\" trong đợt \"{brow['name']}\" — {AUDIT_SCAN_STATUS_LABEL[scan_status]}', actor=actor)
                        conn.commit()
                        result_item = conn.execute('SELECT * FROM audit_items WHERE id=?', (item['id'],)).fetchone()
                        conn.close()
                        return (200, {'result': scan_status, 'item': audit_item_to_dict(result_item)})
@route('POST', '/api/audit/items/([a-zA-Z0-9]+)/update')
@guard('audit', 'edit')
def r_audit_item_update(m, body, qs):
    iid = m.group(1)
    conn = get_db()
    item = conn.execute('SELECT * FROM audit_items WHERE id=?', (iid,)).fetchone()
    if not item:
        conn.close()
        return err('Không tìm thấy hạng mục kiểm kê', 404)
    else:
        brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (item['batch_id'],)).fetchone()
        if brow and brow['status'] != 'active':
            conn.close()
            return err('Đợt kiểm kê đã đóng, không thể chỉnh sửa')
        else:
            actual_status = body.get('actualStatus', item['actual_status'] or item['snap_status'] or '')
            note = body.get('note', item['note'] or '')
            cond_match = 'match' if actual_status == (item['snap_status'] or '') else 'mismatch'
            new_scan_status = item['scan_status']
            if item['scan_status'] in ['matched', 'mismatch']:
                new_scan_status = 'matched' if item['location_match'] == 'match' and cond_match == 'match' else 'mismatch'
            conn.execute('UPDATE audit_items SET actual_status=?, condition_match=?, scan_status=?, note=? WHERE id=?', (actual_status, cond_match, new_scan_status, note, iid))
            account = current_account()
            actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
            log_action(conn, 'Kiểm kê', 'Cập nhật', f'Ghi nhận tình trạng thực tế cho \"{item['asset_code']}\"', actor=actor)
            conn.commit()
            row = conn.execute('SELECT * FROM audit_items WHERE id=?', (iid,)).fetchone()
            conn.close()
            return (200, audit_item_to_dict(row))
@route('POST', '/api/audit/batches/([a-zA-Z0-9]+)/close')
@guard('audit', 'edit')
def r_audit_batch_close(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
    if not brow:
        conn.close()
        return err('Không tìm thấy đợt kiểm kê', 404)
    else:
        if brow['status'] != 'active':
            conn.close()
            return err('Đợt kiểm kê này đã được đóng hoặc hủy trước đó')
        else:
            conn.execute('UPDATE audit_items SET scan_status=\'not_found\' WHERE batch_id=? AND scan_status=\'pending\'', (bid,))
            conn.execute('UPDATE audit_batches SET status=\'completed\', completed_at=? WHERE id=?', (now_ms(), bid))
            account = current_account()
            actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
            log_action(conn, 'Kiểm kê', 'Đóng đợt', f'Đóng đợt kiểm kê \"{brow['name']}\" ({brow['code']})', actor=actor)
            conn.commit()
            row = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
            result = audit_batch_to_dict(row, counts=_audit_batch_counts(conn, bid))
            conn.close()
            return (200, result)
@route('POST', '/api/audit/batches/([a-zA-Z0-9]+)/cancel')
@guard('audit', 'edit')
def r_audit_batch_cancel(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
    if not brow:
        conn.close()
        return err('Không tìm thấy đợt kiểm kê', 404)
    else:
        if brow['status'] != 'active':
            conn.close()
            return err('Đợt kiểm kê này không ở trạng thái đang tiến hành')
        else:
            conn.execute('UPDATE audit_batches SET status=\'cancelled\' WHERE id=?', (bid,))
            account = current_account()
            actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
            log_action(conn, 'Kiểm kê', 'Hủy đợt', f'Hủy đợt kiểm kê \"{brow['name']}\" ({brow['code']})', actor=actor)
            conn.commit()
            conn.close()
            return (200, {'ok': True})
@route('DELETE', '/api/audit/batches/([a-zA-Z0-9]+)')
@guard('audit', 'edit')
def r_audit_batch_delete(m, body, qs):
    bid = m.group(1)
    conn = get_db()
    brow = conn.execute('SELECT * FROM audit_batches WHERE id=?', (bid,)).fetchone()
    if not brow:
        conn.close()
        return err('Không tìm thấy đợt kiểm kê', 404)
    else:
        conn.execute('DELETE FROM audit_items WHERE batch_id=?', (bid,))
        conn.execute('DELETE FROM audit_batches WHERE id=?', (bid,))
        account = current_account()
        actor = account and (account.get('fullName') or account.get('username')) or 'Hệ thống'
        log_action(conn, 'Kiểm kê', 'Xóa đợt', f'Xóa đợt kiểm kê \"{brow['name']}\" ({brow['code']})', actor=actor)
        conn.commit()
        conn.close()
        return (200, {'ok': True})
def _qr_text_for_device(conn, dev_id):
    """Tạo văn bản mã hoá vào QR, bao gồm toàn bộ thông tin quan trọng của thiết bị."""
    row = conn.execute('SELECT * FROM devices WHERE id=?', (dev_id,)).fetchone()
    if not row:
        return
    else:
        d = device_to_dict(row)
        def lookup(table, col, val):
            r = conn.execute(f'SELECT name FROM {table} WHERE {col}=?', (val,)).fetchone()
            if r:
                return r['name']
            else:
                return ''
        dept = lookup('departments', 'id', d.get('deptId', ''))
        pos = lookup('positions', 'id', d.get('posId', ''))
        grp = lookup('groups_tbl', 'id', d.get('groupId', ''))
        typ = lookup('types_tbl', 'id', d.get('typeId', ''))
        usr_r = conn.execute('SELECT full_name, position FROM users WHERE id=?', (d.get('userId', ''),)).fetchone() if d.get('userId') else None
        usr = usr_r['full_name'] + (' - ' + usr_r['position'] if usr_r['position'] else '') if usr_r else ''
        lines = [f'MA TAI SAN: {d.get('assetCode', '')}', f'LOAI: {typ} ({grp})', f'MODEL: {d.get('model', '')}', f'SERIAL: {d.get('serial', '')}', f'PHONG/BAN: {dept}', f'VI TRI: {pos}']
        if usr:
            lines.append(f'NGUOI DUNG: {usr}')
        lines.append(f'TINH TRANG: {d.get('status', '')}')
        if d.get('computerName'):
            lines.append(f'TEN MAY: {d['computerName']}')
        if d.get('cpu'):
            lines.append(f'CPU: {d['cpu']}')
        if d.get('ram'):
            lines.append(f'RAM: {d['ram']}')
        if d.get('storage'):
            lines.append(f'O CUNG: {d['storage']}')
        if d.get('osName'):
            lines.append(f'HDH: {d['osName']}')
        return '\n'.join(lines)
def _matrix_to_svg(matrix, size=300):
    """Chuyển ma trận QR boolean thành SVG inline."""
    n = len(matrix)
    cell = size / n
    rects = []
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = round(c * cell, 3)
                y = round(r * cell, 3)
                w = round(cell + 0.5, 3)
                rects.append(f'<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{w}\"/>')
    paths = ''.join(rects)
    return f'<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {size} {size}\" width=\"{size}\" height=\"{size}\" shape-rendering=\"crispEdges\"><rect width=\"{size}\" height=\"{size}\" fill=\"white\"/><g fill=\"#0a1f2b\">{paths}</g></svg>'
def _crc16_ccitt(data):
    """CRC16-CCITT (0xFFFF, poly 0x1021) — dùng cho chuẩn VietQR/EMVCo."""
    crc = 65535
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 32768:
                crc = (crc << 1 ^ 4129) & 65535
            else:
                crc = crc << 1 & 65535
    return format(crc, '04X')
def _tlv(tag, value):
    return f'{tag}{len(value):02d}{value}'
def _vietqr_payload(bank_bin, account_no, account_name='', amount=None, message=None, city='HO CHI MINH'):
    """Sinh chuỗi mã QR chuyển khoản chuẩn VietQR (NAPAS 247 / EMVCo) — không cần internet để tạo hay quét."""
    sub38 = _tlv('00', bank_bin) + _tlv('01', account_no)
    merchant = _tlv('00', 'A000000727') + _tlv('01', sub38) + _tlv('02', 'QRIBFTTA')
    fields = [_tlv('00', '01'), _tlv('01', '12' if amount else '11'), _tlv('38', merchant), _tlv('52', '0000'), _tlv('53', '704')]
    if amount:
        fields.append(_tlv('54', str(int(amount))))
    fields.append(_tlv('58', 'VN'))
    if account_name:
        fields.append(_tlv('59', account_name[:25]))
    fields.append(_tlv('60', city[:15]))
    if message:
        fields.append(_tlv('62', _tlv('08', message[:25])))
    payload = ''.join(fields) + '6304'
    crc = _crc16_ccitt(payload.encode('utf-8'))
    return payload + crc
@route('GET', '/api/qr/bank')
def r_qr_bank(m, body, qs):
    try:
        bank_bin=(qs.get('bin') or ['970436'])[0]; account_no=(qs.get('acc') or ['0431000196704'])[0]; account_name=(qs.get('name') or ['LE XUAN VIEN'])[0]
        size=max(100,min(600,int((qs.get('size') or ['300'])[0])))
        text=_vietqr_payload(bank_bin,account_no,account_name); matrix=qrgen.generate_qr_matrix(text,level=qrgen.EC_M); return (200,_matrix_to_svg(matrix,size=size))
    except Exception as e: return (500,{'error':str(e)})
@route('GET', '/api/qr/([a-zA-Z0-9]+)')
def r_qr_svg(m, body, qs):
    conn=get_db(); text=_qr_text_for_device(conn,m.group(1)); conn.close()
    if not text: return (404,{'error':'Device not found'})
    try:
        size=max(100,min(600,int((qs.get('size') or ['300'])[0]))); matrix=qrgen.generate_qr_matrix(text,level=qrgen.EC_M); return (200,_matrix_to_svg(matrix,size=size))
    except Exception as e: return (500,{'error':str(e)})
@route('GET', '/api/users/import-template')
@guard('users', 'edit')
def r_users_import_template(m, body, qs):
    conn = get_db()
    try:
        dept0 = conn.execute('SELECT * FROM departments ORDER BY name').fetchone()
        pos0 = conn.execute('SELECT * FROM positions WHERE dept_id=? ORDER BY name', (dept0['id'],)).fetchone() if dept0 else None
        headers = ['Họ và tên *', 'Phòng/Ban', 'Vị trí cụ thể', 'Chức vụ / Vị trí công việc', 'Email', 'Điện thoại', 'Ghi chú']
        example = ['Nguyễn Văn A', dept0['name'] if dept0 else 'Phòng Kế toán', pos0['name'] if pos0 else '', 'Nhân viên', 'nguyenvana@example.com', '0901234567', '']
        ref_headers = ['Phòng/Ban', 'Vị trí cụ thể']
        ref_rows = [[dp[0], dp[1]] for dp in _dept_pos_pairs(conn)]
        xlsx_bytes = build_xlsx([('Nhập người dùng', headers, [example]), ('Danh mục tham khảo', ref_headers, ref_rows)])
    finally:
        conn.close()
    return (200, {'filename': 'mau_nhap_nguoi_dung.xlsx', 'base64': base64.b64encode(xlsx_bytes).decode('ascii')})
@route('POST', '/api/users/import')
@guard('users', 'edit')
def r_users_import(m, body, qs):
    b64=(body or {}).get('fileBase64') or ''
    if not b64: return err('Vui lòng chọn file Excel để nhập')
    try: rows=parse_xlsx_first_sheet(base64.b64decode(b64))
    except Exception as e: return err(f'Không đọc được file Excel: {e}')
    if len(rows)<2: return err('File Excel không có dữ liệu')
    conn=get_db(); success=0; errors=[]
    try:
        depts={(d['name'] or '').strip().lower():d for d in conn.execute('SELECT * FROM departments')}; pos={(r['dept_id'],(r['name'] or '').strip().lower()):r for r in conn.execute('SELECT * FROM positions')}
        for n,row in enumerate(rows[1:],2):
            def c(i): return str(row[i]).strip() if i<len(row) and row[i] is not None else ''
            name=c(0)
            if not name: continue
            dept_id=pos_id=''; d=depts.get(c(1).lower()) if c(1) else None
            if c(1) and not d: errors.append(f'Dòng {n}: không tìm thấy Phòng/Ban "{c(1)}"')
            elif d:
                dept_id=d['id']; pr=pos.get((dept_id,c(2).lower())) if c(2) else None
                if c(2) and not pr: errors.append(f'Dòng {n}: không tìm thấy Vị trí "{c(2)}"')
                elif pr: pos_id=pr['id']
            conn.execute('INSERT INTO users(id,full_name,email,phone,dept_id,pos_id,position,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(new_id(),name,c(4),c(5),dept_id,pos_id,c(3),c(6),now_ms())); success+=1
        conn.commit()
    except Exception as e: conn.rollback(); return err(f'Lỗi nhập người dùng: {e}',500)
    finally: conn.close()
    return (200,{'success':success,'total':len(rows)-1,'errors':errors})
@route('GET', '/api/users')
@guard('users', 'view')
def r_users_list(m, body, qs):
    conn = get_db()
    users = [user_to_dict(r) for r in conn.execute('SELECT * FROM users ORDER BY full_name')]
    conn.close()
    return (200, {'items': users})
@route('POST', '/api/users')
@guard('users', 'edit')
def r_users_create(m, body, qs):
    name = (body.get('fullName') or '').strip()
    if not name:
        return err('Tên người dùng không được để trống')
    else:
        conn = get_db()
        uid = new_id()
        conn.execute('INSERT INTO users(id,full_name,email,phone,dept_id,pos_id,position,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (uid, name, body.get('email', '').strip(), body.get('phone', '').strip(), body.get('deptId', ''), body.get('posId', ''), body.get('position', '').strip(), body.get('note', '').strip(), now_ms()))
        log_action(conn, 'Người dùng', 'Thêm mới', f'Thêm người dùng \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        conn.close()
        return (200, user_to_dict(r))
@route('PUT', '/api/users/([a-zA-Z0-9]+)')
@guard('users', 'edit')
def r_users_update(m, body, qs):
    uid = m.group(1)
    name = (body.get('fullName') or '').strip()
    if not name:
        return err('Tên người dùng không được để trống')
    else:
        conn = get_db()
        conn.execute('UPDATE users SET full_name=?,email=?,phone=?,dept_id=?,pos_id=?,position=?,note=? WHERE id=?', (name, body.get('email', '').strip(), body.get('phone', '').strip(), body.get('deptId', ''), body.get('posId', '').strip(), body.get('position', '').strip(), body.get('note', '').strip(), uid))
        log_action(conn, 'Người dùng', 'Cập nhật', f'Cập nhật người dùng \"{name}\"')
        conn.commit()
        r = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        conn.close()
        return (200, user_to_dict(r))
@route('DELETE', '/api/users/([a-zA-Z0-9]+)')
@guard('users', 'edit')
def r_users_delete(m, body, qs):
    uid = m.group(1)
    conn = get_db()
    urow = conn.execute('SELECT full_name FROM users WHERE id=?', (uid,)).fetchone()
    conn.execute('UPDATE devices SET user_id=\'\' WHERE user_id=?', (uid,))
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    log_action(conn, 'Người dùng', 'Xóa', f"Xóa người dùng \"{(urow['full_name'] if urow else uid)}\"")
    conn.commit()
    conn.close()
    return (200, {'ok': True})
@route('POST', '/api/restore')
@require_admin
def r_restore(m, body, qs):
    if not isinstance(body,dict) or 'departments' not in body: return err('Dữ liệu sao lưu không hợp lệ')
    conn=get_db()
    try:
        for table in ['borrows','maintenance','material_txn','materials','suppliers','users','history','devices','types_tbl','groups_tbl','positions','departments']:
            conn.execute(f'DELETE FROM {table}')
        for d in body.get('departments',[]):
            conn.execute('INSERT INTO departments(id,name,branch_id) VALUES(?,?,?)',(d['id'],d.get('name',''),d.get('branchId') or None))
            for po in d.get('positions',[]): conn.execute('INSERT INTO positions(id,dept_id,name) VALUES(?,?,?)',(po['id'],d['id'],po.get('name','')))
        for g in body.get('groups',[]):
            conn.execute('INSERT INTO groups_tbl(id,name) VALUES(?,?)',(g['id'],g.get('name','')))
            for t in g.get('types',[]): conn.execute('INSERT INTO types_tbl(id,group_id,name) VALUES(?,?,?)',(t['id'],g['id'],t.get('name','')))
        for u in body.get('users',[]): conn.execute('INSERT INTO users(id,full_name,email,phone,dept_id,pos_id,position,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(u['id'],u.get('fullName',''),u.get('email',''),u.get('phone',''),u.get('deptId',''),u.get('posId',''),u.get('position',''),u.get('note',''),u.get('createdAt',now_ms())))
        for dev in body.get('devices',[]):
            vals=(dev.get('id') or new_id(),dev.get('assetCode',''),dev.get('deptId',''),dev.get('posId',''),dev.get('groupId',''),dev.get('typeId',''),dev.get('model',''),dev.get('manufacturer',''),dev.get('serial',''),dev.get('config',''),dev.get('status','Bình thường'),dev.get('importDate',''),dev.get('allocateDate',''),dev.get('warrantyMonths',''),dev.get('warrantyUnit','Tháng'),dev.get('supplier',''),dev.get('value',0),dev.get('note',''),dev.get('createdAt',now_ms()))
            conn.execute('INSERT INTO devices(id,asset_code,dept_id,pos_id,group_id,type_id,model,manufacturer,serial,config,status,import_date,allocate_date,warranty_months,warranty_unit,supplier,value,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',vals)
        for k,v in body.get('counters',{}).items(): conn.execute('INSERT INTO counters([key],value) VALUES(?,?)',(k,v))
        ensure_warehouse_location(conn); ensure_default_account(conn); conn.commit(); return (200,{'ok':True})
    except Exception as e: conn.rollback(); return err(f'Lỗi khôi phục: {e}',500)
    finally: conn.close()
class AppHandler(BaseHTTPRequestHandler):
    """AppHandler"""
    def log_message(self, format, *args):
        return
    def do_GET(self):
        self._handle('GET')
    def do_POST(self):
        self._handle('POST')
    def do_PUT(self):
        self._handle('PUT')
    def do_DELETE(self):
        self._handle('DELETE')
    def _handle(self, method):
        parsed = urlsplit(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        body = None
        if method in ['POST', 'PUT']:
            length = int(self.headers.get('Content-Length', 0))
            if length > 0:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode('utf-8'))
                except Exception:
                    body = {}
        _local.account = None
        _local.token = None
        PUBLIC_API_PATHS = {'/api/login'}
        needs_auth = path.startswith('/api/') and path not in PUBLIC_API_PATHS and (not path.startswith('/api/qr/'))
        if needs_auth:
            token = None
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:].strip()
            session = SESSIONS.get(token) if token else None
            if not session:
                self._respond(401, {'error': 'Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại', 'authRequired': True})
                return
            else:
                _local.account = session
                _local.token = token
        for m_method, regex, fn in ROUTES:
            if m_method == method:
                match = regex.match(path)
                if match:
                    code, res = fn(match, body, qs)
                    if path.startswith('/api/qr/') and isinstance(res, str):
                        svg_bytes = res.encode('utf-8')
                        self.send_response(code)
                        self.send_header('Content-Type', 'image/svg+xml; charset=utf-8')
                        self.send_header('Cache-Control', 'no-cache')
                        self.send_header('Content-Length', str(len(svg_bytes)))
                        self.end_headers()
                        self.wfile.write(svg_bytes)
                        return
                    else:
                        self._respond(code, res, download=path == '/api/backup')
                        return
        if method == 'GET' and (path == '/' or path == '/index.html') and os.path.exists(STATIC_INDEX):
            with open(STATIC_INDEX, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            if method == 'GET':
                dm = re.match('^/device/([a-zA-Z0-9]+)$', path)
                if dm:
                    dev_id = dm.group(1)
                    conn = get_db()
                    row = conn.execute('SELECT * FROM devices WHERE id=?', (dev_id,)).fetchone()
                    if row:
                        d = device_to_dict(row)
                        dept_row = conn.execute('SELECT name FROM departments WHERE id=?', (d['deptId'],)).fetchone()
                        pos_row = conn.execute('SELECT name FROM positions  WHERE id=?', (d['posId'],)).fetchone()
                        grp_row = conn.execute('SELECT name FROM groups_tbl WHERE id=?', (d['groupId'],)).fetchone()
                        typ_row = conn.execute('SELECT name FROM types_tbl  WHERE id=?', (d['typeId'],)).fetchone()
                        usr_row = conn.execute('SELECT full_name,position FROM users WHERE id=?', (d['userId'],)).fetchone() if d.get('userId') else None
                        dept_name = dept_row['name'] if dept_row else '—'
                        pos_name = pos_row['name'] if pos_row else '—'
                        grp_name = grp_row['name'] if grp_row else '—'
                        typ_name = typ_row['name'] if typ_row else '—'
                        usr_name = usr_row['full_name'] if usr_row else '—'
                        usr_pos = usr_row['position'] if usr_row and usr_row['position'] else ''
                        conn.close()
                        def esc(s):
                            return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\"', '&quot;')
                        def row_html(label, value, mono=False):
                            if not value or value == '—':
                                return ''
                            else:
                                v = f'<code style=\"font-family:monospace;\">{esc(value)}</code>' if mono else esc(value)
                                return f'<tr><td class=\"lbl\">{esc(label)}</td><td>{v}</td></tr>'
                        hw_rows = ''.join([row_html('Tên máy tính', d.get('computerName')), row_html('CPU', d.get('cpu')), row_html('RAM', d.get('ram')), row_html('Ổ cứng', d.get('storage')), row_html('Card đồ họa', d.get('gpu')), row_html('Mainboard', d.get('mainboard')), row_html('Hệ điều hành', d.get('osName')), row_html('System Model', d.get('systemModel'), mono=True), row_html('MAC Vật lý', d.get('macPhysical'), mono=True)])
                        hw_section = f'<div class=\"section\">Cấu hình chi tiết</div>\n                    <table>{hw_rows}</table>' if hw_rows else ''
                        html = f'<!DOCTYPE html>\n<html lang=\"vi\"><head><meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<title>{esc(d['assetCode'])} — {esc(typ_name)}</title>\n<style>\n  *{box-sizing:border-box;} body{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;\n    background:#f0f5f4;color:#0f2027;margin:0;padding:16px;}\n  .card{background:#fff;border-radius:14px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:520px;margin:0 auto;}\n  .badge{display:inline-block;background:#0d9488;color:#fff;border-radius:99px;padding:3px 12px;font-size:12px;font-weight:700;margin-bottom:10px;}\n  .asset{font-size:26px;font-weight:800;color:#0d2836;letter-spacing:.04em;margin-bottom:4px;}\n  .type{font-size:14px;color:#4b6266;margin-bottom:16px;}\n  .section{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#0d9488;\n    margin:16px 0 8px;padding-bottom:4px;border-bottom:1px solid #e4ebe9;}\n  table{width:100%;border-collapse:collapse;font-size:13px;}\n  td{padding:7px 0;border-bottom:1px solid #f0f5f4;vertical-align:top;}\n  td.lbl{color:#4b6266;font-weight:600;min-width:130px;}\n  code{background:#f0f5f4;border-radius:4px;padding:1px 6px;font-size:12px;}\n  .footer{text-align:center;font-size:11px;color:#9aa8ad;margin-top:20px;}\n</style></head><body>\n<div class=\"card\">\n  <div class=\"badge\">{esc(grp_name)}</div>\n  <div class=\"asset\">{esc(d['assetCode'])} — {esc(d.get('model', ''))}\n    {row_html('Phòng/Ban', dept_name)}\n    {row_html('Nhà cung cấp', d.get('supplier', ''))}\n    {row_html('Tình trạng', d.get('status', ''), mono=True)}\n  </table>\n  {hw_section}\n  <div class=\"footer\">QLTB PRO &nbsp;·&nbsp; Quét mã QR để xem thông tin thiết bị</div>\n</div></body></html>'
                        page = html.encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html; charset=utf-8')
                        self.send_header('Content-Length', str(len(page)))
                        self.end_headers()
                        self.wfile.write(page)
                    else:
                        conn.close()
                        err_page = b'<h2>Kh\xc3\xb4ng t\xc3\xacm th\xe1\xba\xa5y thi\xe1\xba\xbft b\xe1\xbb\x8b</h2>'
                        self.send_response(404)
                        self.send_header('Content-Type', 'text/html; charset=utf-8')
                        self.send_header('Content-Length', str(len(err_page)))
                        self.end_headers()
                        self.wfile.write(err_page)
            self._respond(404, {'error': 'Not Found'})
    def _respond(self, code, data, download=False):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if download:
            filename = f"qltb_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            self.send_header('Content-Disposition', f'attachment; filename=\"{filename}\"')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
def main():
    init_db()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), AppHandler)
    url = f'http://10.210.12.112:{PORT}/'
    print(f'Server dang chay tai: {url}')
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nDa dung server.')

if __name__ == '__main__':
    main()
