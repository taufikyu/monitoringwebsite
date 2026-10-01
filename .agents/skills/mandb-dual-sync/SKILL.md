---
name: mandb-dual-sync
description: Sinkronisasi dan pemeliharaan fitur dua arah (Dual-Stack Sync) antara OpenSIPKD MANDB (Pyramid di c:\simulasi\mandb\apps\mandb\mandb) dan Flask Monitor (Flask + SQLite di C:\develop\monitor). Aktifkan skill ini secara otomatis setiap kali ada penambahan fitur baru, perbaikan bug, modifikasi logika SSH/remote execution, perubahan skema database, atau pembaruan antarmuka (UI/templates) agar kedua codebase selalu sinkron dan setara.
---

# MANDB & Flask Monitor Dual-Stack Synchronization Guidelines

Panduan standar sinkronisasi dan pemeliharaan dua arah (**Dual-Stack Sync**) antara aplikasi **OpenSIPKD MANDB** (`c:\simulasi\mandb\apps\mandb\mandb`) dan aplikasi **Flask Monitor** (`C:\develop\monitor`).

---

## 1. Prinsip Utama Dual-Stack Sync

Setiap kali ada penambahan fitur, perbaikan bug, optimasi remote SSH, perubahan model database, atau pembaruan UI pada modul pemantauan server:
1. **Wajib Diimplementasikan di Kedua Sisi**: Setiap perubahan logika bisnis atau fitur harus diterapkan pada **kedua proyek** (OpenSIPKD MANDB dan Flask Monitor).
2. **Kesesuaian Framework**:
   - **OpenSIPKD**: Menggunakan pola Pyramid `BaseView`, `routes.csv`, Deform/Colander, Chameleon template (`.pt`), dan PostgreSQL backend.
   - **Flask Monitor**: Menggunakan pola Flask Blueprint (`modules/monitor`), Flask-SQLAlchemy, Jinja2 template (`.html`), dan SQLite backend (`instance/monitor.db`).
3. **Paritas Fitur (Feature Parity)**: Pastikan parameter request, respon JSON AJAX, kemampuan multi-hop SSH, status service UP/DOWN, dan audit log memiliki fungsionalitas yang identik di kedua sisi.

---

## 2. Matriks Pemetaan Komponen (Codebase Mapping)

| Komponen | OpenSIPKD MANDB (`c:\simulasi\mandb\apps\mandb`) | Flask Monitor (`C:\develop\monitor`) |
| :--- | :--- | :--- |
| **Engine SSH & Tunnel** | `mandb/scripts/ssh_helper.py` | `modules/monitor/ssh_helper.py` |
| **Models & Schema** | `mandb/models.py` | `modules/monitor/models.py` |
| **Server CRUD & Status** | `mandb/views/server.py` | `modules/monitor/routes.py` (`/servers`) |
| **Multi-Apps & Dual Status**| `mandb/views/server_app.py` | `modules/monitor/routes.py` (`/apps`) |
| **Jump Host Routing** | `mandb/views/server_jump.py` | `modules/monitor/routes.py` (`/jumps`) |
| **SQL Query Console** | `mandb/views/query.py` | `modules/monitor/routes.py` (`/query`) |
| **Audit Logs** | N/A (Web logs) | `modules/monitor/audit_logger.py` & `routes.py` (`/logs`) |
| **Routing Definition** | `mandb/routes.csv` | `modules/monitor/routes.py` & `modules/monitor/__init__.py` |
| **Templates / UI** | `mandb/views/templates/*.pt` (ZPT/Chameleon) | `modules/monitor/templates/monitor/*.html` (Jinja2) |
