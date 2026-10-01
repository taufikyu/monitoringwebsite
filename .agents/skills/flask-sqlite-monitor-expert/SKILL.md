---
name: flask-sqlite-monitor-expert
description: >-
  Standard operating procedures and architecture patterns for Flask, SQLite, Flask-SQLAlchemy, and Jinja2
  in the Server & Service Monitor application. Covers zero-config SQLite instance management, multi-hop SSH
  tunneling, remote service status inspection (systemd/supervisor), and safe audit trail persistence.
---

# Flask & SQLite Monitor Architecture Guide

Panduan standar arsitektur dan pengembangan aplikasi **Server & Service Monitor** (`C:\develop\monitor`) menggunakan Flask, SQLite, Flask-SQLAlchemy, Paramiko SSH Tunneling, dan Jinja2 Glass-Morphism UI.

---

## 🗄️ 1. SQLite Single-File Database Architecture

Aplikasi menggunakan backend **SQLite 3** mandiri yang tersimpan di `instance/monitor.db`.

1. **Path Resolution**:
   - Selalu resolve path absolut file database menggunakan `Path(__file__).resolve().parent / 'instance' / 'monitor.db'` untuk mencegah error `unable to open database file` di Windows.
2. **Auto Table Creation & Auto-Sync**:
   - `db.create_all()` dijalankan di `app.app_context()` saat inisialisasi aplikasi.
   - Pendaftaran permission dan menu navigasi modul monitor disinkronkan secara otomatis melalui `sync_monitor_permissions_and_menus()`.
3. **Safe Transactions & Rollback**:
   - Setiap operasi database non-kritis (seperti audit log dan query history) wajib dibungkus dalam `try-except` dengan `db.session.rollback()` defensif agar tidak mengganggu alur kerja utama pengguna.

---

## 🔌 2. SSH Multi-Hop Tunneling & Remote Execution Engine

Mesin eksekusi remote berada pada `modules/monitor/ssh_helper.py`:

1. **Multi-Hop Bastion Routing**:
   - Menggunakan `_build_jump_chain(server_id)` yang secara rekursif membangun rantai `paramiko.Transport.open_channel("direct-tcpip")` melalui jump server perantara hingga mencapai target host internal.
2. **Dual Status Inspection (SSH + Service UP/DOWN)**:
   - Cek SSH handshake via `test_ssh_connection(server_id)`.
   - Cek status service realtime via `check_remote_service_status(app_id)` yang mendukung Systemd (`systemctl is-active/status`) dan Supervisor (`supervisorctl status`).
3. **Remote Service Restart via Sudo**:
   - Perintah restart service dijalankan aman dengan pipe password sudo jika diperlukan via `restart_remote_service()`.

---

## 🛡️ 3. Multi-User Sandbox & RBAC Access Control

1. **Tingkat Akses (Levels)**:
   - **Level 3 (Administrator)**: Hak akses penuh ke seluruh menu, user management, role groups, server configuration, dan remote restart service.
   - **Level 2 (Pro / Operator)**: Akses ke SQL query console dan audit activity logs.
   - **Level 1 (Member / Viewer)**: Akses baca ke dashboard dan daftar status server.
2. **Granular Permissions**:
   - Gunakan decorator `@permission_required('admin.servers')`, `@permission_required('admin.service')`, dsb. pada endpoint sensitif.

---

## 🎨 4. Frontend Glass-Morphic UI & Live AJAX Polling

1. **Live Status Cards**:
   - Kartu status aplikasi di dashboard menampilkan badge ganda (SSH Aktif/Non-Aktif dan Service UP/DOWN).
2. **Mass Testing (`ThreadPoolExecutor`)**:
   - Fitur "Cek Semua Status" menggunakan `ThreadPoolExecutor(max_workers=5)` di backend dan merespon JSON ke frontend untuk memperbarui badge secara realtime tanpa me-reload halaman.
3. **Modal Terminal**:
   - Output log status service dirender di modal terminal hitam dengan pre-wrap formatting.
