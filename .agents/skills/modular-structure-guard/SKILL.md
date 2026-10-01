---
name: modular-structure-guard
description: >-
  Menjaga arsitektur modular monitor: DILARANG menempatkan file .py milik
  aplikasi di root folder (sejajar app.py). Semua file Python harus berada di
  dalam subdirektori (core/, modules/, atau folder bernama lainnya).
---

# Modular Structure Guard — monitor

Skill ini WAJIB diikuti setiap kali membuat, memindahkan, atau mengedit file Python di proyek `C:\develop\monitor`.

---

## ❌ Aturan Utama: Tidak Ada .py di Root

**DILARANG KERAS** menempatkan file `.py` milik aplikasi langsung di root proyek (`C:\develop\monitor\`) sejajar dengan `app.py`.

### Root folder HANYA boleh berisi:
```
C:\develop\monitor\
├── app.py               ← WSGI entry point (satu-satunya .py di root)
├── requirements.txt
├── runweb.bat
├── .env
├── instance/            ← Database SQLite (monitor.db)
│   └── monitor.db
├── core/                ← platform-level core modules
│   ├── __init__.py
│   ├── config.py
│   ├── extensions.py
│   ├── models.py
│   └── decorators.py
└── modules/             ← feature modules
    └── monitor/
        ├── __init__.py
        ├── models.py
        ├── routes.py
        ├── ssh_helper.py
        ├── audit_logger.py
        └── templates/
            └── monitor/
```

---

## 📁 Aturan Penempatan File

| Jenis file | Lokasi yang benar |
|---|---|
| Config, env vars, path global | `core/config.py` |
| SQLAlchemy db, login_manager, bcrypt | `core/extensions.py` |
| Model User, AppMenu, Role, Permission | `core/models.py` |
| Model fitur Server / Monitor | `modules/monitor/models.py` |
| Logic SSH / Remote Execution | `modules/monitor/ssh_helper.py` |
| Blueprint / routes modul monitor | `modules/monitor/routes.py` |
| Audit Logger | `modules/monitor/audit_logger.py` |
| Shared utility / decorator | `core/decorators.py` atau `core/utils.py` |
| Template fitur monitor | `modules/monitor/templates/monitor/*.html` |
| Template global / core | `templates/*.html` |

---

## ✅ Import Convention

```python
# Di dalam modules/monitor/*.py:
from core.extensions import db
from core.models import User, AppMenu, Role, Permission
from core.decorators import permission_required
from modules.monitor.models import Server, ServerApp, ServerJump

# Di app.py (root entry point):
from core.extensions import db, login_manager, bcrypt, oauth, mail
from core.models import User, AppMenu, Role, Permission
from modules.monitor import init_app as init_monitor_app, sync_monitor_permissions_and_menus
```

---

## 🔍 Audit Command

```powershell
# Cek .py di root (boleh HANYA app.py)
Get-ChildItem -Path "C:\develop\monitor" -MaxDepth 1 -Filter "*.py" | Select-Object Name
# Output yang diizinkan: HANYA app.py
```
