from __future__ import annotations
from flask import Flask
from core.extensions import db
from core.models import AppMenu, Permission, Role
from .routes import monitor_bp
from . import models

MONITOR_PERMISSIONS = [
    {
        'code': 'admin.servers',
        'name': 'Kelola Server & Host',
        'category': 'Server Monitoring',
        'description': 'Menambah, mengubah, menguji SSH, dan menghapus server.'
    },
    {
        'code': 'admin.apps',
        'name': 'Kelola Multi-Aplikasi',
        'category': 'Server Monitoring',
        'description': 'Mengatur aplikasi terpasang, cek SSH & Service UP/DOWN.'
    },
    {
        'code': 'admin.jumps',
        'name': 'Kelola Jump Host Routing',
        'category': 'Server Monitoring',
        'description': 'Mengatur rute multi-hop jump server proxy.'
    },
    {
        'code': 'admin.service',
        'name': 'Restart Remote Service',
        'category': 'Server Monitoring',
        'description': 'Menjalankan restart remote service via sudo root.'
    },
    {
        'code': 'admin.query',
        'name': 'Akses SQL Query Console',
        'category': 'Server Monitoring',
        'description': 'Mengeksekusi SQL query langsung ke remote database.'
    },
    {
        'code': 'admin.logs',
        'name': 'Lihat Audit Activity Logs',
        'category': 'Server Monitoring',
        'description': 'Melihat catatan audit log aktivitas seluruh pengguna.'
    }
]

MONITOR_MENUS = [
    {
        'key': 'monitor_dashboard',
        'title': 'Server Monitor',
        'route': 'monitor.dashboard',
        'icon': 'ph ph-gauge',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.servers',
        'min_level': 1,
        'order': 10,
        'endpoints': 'monitor.dashboard'
    },
    {
        'key': 'monitor_servers',
        'title': 'Daftar Server',
        'route': 'monitor.server_list',
        'icon': 'ph ph-hard-drives',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.servers',
        'min_level': 1,
        'order': 11,
        'endpoints': 'monitor.server_list,monitor.server_add,monitor.server_edit'
    },
    {
        'key': 'monitor_apps',
        'title': 'Aplikasi Terpasang',
        'route': 'monitor.app_list',
        'icon': 'ph ph-cube',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.apps',
        'min_level': 1,
        'order': 12,
        'endpoints': 'monitor.app_list,monitor.app_add,monitor.app_edit'
    },
    {
        'key': 'monitor_jumps',
        'title': 'Jump Host Routing',
        'route': 'monitor.jump_list',
        'icon': 'ph ph-git-merge',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.jumps',
        'min_level': 1,
        'order': 13,
        'endpoints': 'monitor.jump_list,monitor.jump_add'
    },
    {
        'key': 'monitor_query',
        'title': 'SQL Console',
        'route': 'monitor.sql_query',
        'icon': 'ph ph-code',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.query',
        'min_level': 2,
        'order': 14,
        'endpoints': 'monitor.sql_query'
    },
    {
        'key': 'monitor_logs',
        'title': 'Activity Logs',
        'route': 'monitor.activity_logs',
        'icon': 'ph ph-clock-counter-clockwise',
        'group_name': 'Server Monitoring',
        'permission_code': 'admin.logs',
        'min_level': 2,
        'order': 15,
        'endpoints': 'monitor.activity_logs'
    }
]


def sync_monitor_permissions_and_menus():
    """Mendaftarkan permission dan menu navigasi modul monitor secara otomatis."""
    # 1. Sync Permissions
    for p_data in MONITOR_PERMISSIONS:
        perm = Permission.query.filter_by(code=p_data['code']).first()
        if not perm:
            perm = Permission(
                code=p_data['code'],
                name=p_data['name'],
                category=p_data['category'],
                description=p_data.get('description', '')
            )
            db.session.add(perm)
        else:
            perm.name = p_data['name']
            perm.category = p_data['category']
            perm.description = p_data.get('description', '')

    # 2. Grant permissions to Administrator role
    admin_role = Role.query.filter_by(name='Administrator').first()
    if admin_role:
        for p_data in MONITOR_PERMISSIONS:
            p_obj = Permission.query.filter_by(code=p_data['code']).first()
            if p_obj and p_obj not in admin_role.permissions:
                admin_role.permissions.append(p_obj)
    db.session.commit()

    # 3. Sync Menus
    for item in MONITOR_MENUS:
        existing = AppMenu.query.filter_by(key=item['key']).first()
        if not existing:
            new_menu = AppMenu(
                key=item['key'],
                title=item['title'],
                route=item['route'],
                icon=item['icon'],
                group_name=item['group_name'],
                permission_code=item.get('permission_code'),
                min_level=item['min_level'],
                order=item['order'],
                is_active=True,
                endpoints=item['endpoints']
            )
            db.session.add(new_menu)
        else:
            existing.title = item['title']
            existing.route = item['route']
            existing.icon = item['icon']
            existing.group_name = item['group_name']
            existing.permission_code = item.get('permission_code', existing.permission_code)
            existing.order = item['order']
            existing.endpoints = item['endpoints']
    db.session.commit()


def init_app(app: Flask):
    """Inisialisasi Blueprint modul monitor ke dalam aplikasi Flask."""
    app.register_blueprint(monitor_bp)
