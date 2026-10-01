from __future__ import annotations
import os
import threading
from pathlib import Path
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from dotenv import load_dotenv
from core.extensions import db, login_manager, bcrypt, oauth, mail
from core.models import User, AppMenu, Role, Permission, user_roles, role_permissions
from core.decorators import permission_required
from sqlalchemy import text
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer
from flask_mail import Message
from modules.monitor import init_app as init_monitor_app, sync_monitor_permissions_and_menus

load_dotenv()


# =========================================================================
# CORE RBAC PERMISSIONS & DEFAULT ROLE GROUPS
# =========================================================================

DEFAULT_PERMISSIONS = [
    # --- NAVIGASI UTAMA ---
    {
        'code': 'menu.dashboard',
        'name': 'Akses Dashboard Utama',
        'category': 'Navigasi',
        'description': 'Melihat ringkasan data, statistik, dan widget dashboard.'
    },
    # --- MANAJEMEN SISTEM ---
    {
        'code': 'admin.users',
        'name': 'Kelola Pengguna (User Management)',
        'category': 'Manajemen Sistem',
        'description': 'Menambah, mengubah role/level, dan menghapus akun pengguna.'
    },
    {
        'code': 'admin.roles',
        'name': 'Kelola Role & Hak Akses (Role Groups)',
        'category': 'Manajemen Sistem',
        'description': 'Membuat grup role dan mengatur izin (permission) per grup.'
    },
    {
        'code': 'admin.menus',
        'name': 'Kelola Menu Navigasi (Menu Management)',
        'category': 'Manajemen Sistem',
        'description': 'Mengatur visibilitas show/hide, izin akses, dan urutan menu.'
    }
]

DEFAULT_ROLES = [
    {
        'name': 'Administrator',
        'description': 'Hak akses penuh ke seluruh menu dan manajemen sistem.',
        'is_system': True,
        'permission_codes': [p['code'] for p in DEFAULT_PERMISSIONS]
    },
    {
        'name': 'Member VIP',
        'description': 'Akses fitur umum dan dashboard.',
        'is_system': False,
        'permission_codes': ['menu.dashboard']
    },
    {
        'name': 'Member Free',
        'description': 'Akses dasar pengguna.',
        'is_system': False,
        'permission_codes': ['menu.dashboard']
    }
]


def sync_default_permissions_and_roles():
    """Syncs default system permissions and role groups into database."""
    # 1. Sync Permissions
    for p_data in DEFAULT_PERMISSIONS:
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
    db.session.commit()

    # 2. Sync Roles & assign default permissions
    for r_data in DEFAULT_ROLES:
        role = Role.query.filter_by(name=r_data['name']).first()
        if not role:
            role = Role(
                name=r_data['name'],
                description=r_data.get('description', ''),
                is_system=r_data.get('is_system', False)
            )
            db.session.add(role)
            db.session.commit()
        
        if role.name == 'Administrator' or not role.permissions:
            matched_perms = Permission.query.filter(Permission.code.in_(r_data['permission_codes'])).all()
            role.permissions = matched_perms
            db.session.commit()

    # 3. Ensure system admin has Administrator role
    admin_user = User.query.filter_by(email='system@app.com').first()
    admin_role = Role.query.filter_by(name='Administrator').first()
    if admin_user and admin_role and admin_role not in admin_user.roles:
        admin_user.roles.append(admin_role)
        db.session.commit()


def sync_default_menus():
    """Sync core system default menus into AppMenu database table."""
    default_menus = [
        {
            'key': 'home',
            'title': 'Dashboard',
            'route': 'home',
            'icon': 'ph ph-squares-four',
            'group_name': 'Navigasi',
            'permission_code': 'menu.dashboard',
            'min_level': 1,
            'order': 1,
            'endpoints': 'home'
        },
        {
            'key': 'admin_users',
            'title': 'User Management',
            'route': 'admin_users',
            'icon': 'ph ph-shield-check',
            'group_name': 'Manajemen Sistem',
            'permission_code': 'admin.users',
            'min_level': 3,
            'order': 50,
            'endpoints': 'admin_users,admin_user_add,admin_user_edit,admin_user_delete'
        },
        {
            'key': 'admin_roles',
            'title': 'Role & Permission Group',
            'route': 'admin_roles',
            'icon': 'ph ph-users-three',
            'group_name': 'Manajemen Sistem',
            'permission_code': 'admin.roles',
            'min_level': 3,
            'order': 55,
            'endpoints': 'admin_roles,admin_role_add,admin_role_edit,admin_role_delete,admin_role_sync'
        },
        {
            'key': 'admin_menus',
            'title': 'Menu Management',
            'route': 'admin_menus',
            'icon': 'ph ph-list-dashes',
            'group_name': 'Manajemen Sistem',
            'permission_code': 'admin.menus',
            'min_level': 3,
            'order': 60,
            'endpoints': 'admin_menus,admin_menu_edit,admin_menu_toggle,admin_menu_sync'
        },
    ]

    for item in default_menus:
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
            existing.route = item['route']
            existing.permission_code = item.get('permission_code', existing.permission_code)
            if not existing.endpoints:
                existing.endpoints = item['endpoints']
    db.session.commit()


def create_app():
    app = Flask(__name__)

    # Ensure instance folder exists for SQLite DB
    instance_dir = Path(__file__).resolve().parent / 'instance'
    instance_dir.mkdir(parents=True, exist_ok=True)

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'mandb-flask-monitor-sqlite-key-2026')
    
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        if db_url.startswith('sqlite:///') and not db_url.startswith('sqlite:////'):
            path_part = db_url[len('sqlite:///'):]
            if not os.path.isabs(path_part) and not (len(path_part) > 1 and path_part[1] == ':'):
                db_path = (Path(__file__).resolve().parent / path_part).resolve().as_posix()
                db_url = f"sqlite:///{db_path}"
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        sqlite_file = (instance_dir / 'monitor.db').as_posix()
        app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{sqlite_file}"
        
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 465))
    app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'False').lower() == 'true'
    app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'True').lower() == 'true'
    app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'dummy@gmail.com')
    app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'dummy_app_password')
    app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'dummy@gmail.com')

    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Initialize Core Extensions
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    oauth.init_app(app)
    mail.init_app(app)

    if os.getenv('GOOGLE_CLIENT_ID'):
        oauth.register(
            name='google',
            client_id=os.getenv('GOOGLE_CLIENT_ID'),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'}
        )

    # Register Monitor Module Blueprint
    init_monitor_app(app)

    login_manager.login_view = 'login'
    login_manager.login_message = "Silakan login terlebih dahulu."

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    with app.app_context():
        try:
            db.create_all()
            
            default_email = 'system@app.com'
            system_user = User.query.filter_by(email=default_email).first()
            if not system_user:
                hashed_pw = bcrypt.generate_password_hash('admin').decode('utf-8')
                new_admin = User(
                    email=default_email,
                    password=hashed_pw,
                    name='System Admin',
                    level=3,
                    is_admin=True,
                    is_verified=True
                )
                db.session.add(new_admin)
                db.session.commit()
            else:
                system_user.level = 3
                system_user.is_admin = True
                system_user.is_verified = True
                db.session.commit()
            
            sync_default_permissions_and_roles()
            sync_default_menus()
            sync_monitor_permissions_and_menus()
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            print(f"[DB Init Warning] {e}")

    @app.context_processor
    def inject_modular_menus():
        if not current_user.is_authenticated:
            return dict(modular_menus=[], navigation_groups={}, user_level=0)
        
        user_level = current_user.effective_level if hasattr(current_user, 'effective_level') else (3 if getattr(current_user, 'is_admin', False) else 1)
        try:
            active_menus = AppMenu.query.filter_by(is_active=True).order_by(AppMenu.order.asc(), AppMenu.id.asc()).all()
            visible_menus = []
            for m in active_menus:
                if current_user.is_admin or user_level == 3:
                    visible_menus.append(m)
                elif m.permission_code:
                    if current_user.has_permission(m.permission_code):
                        visible_menus.append(m)
                elif m.min_level <= user_level:
                    visible_menus.append(m)
        except Exception as e:
            print(f"[Menu Fetch Warning] {e}")
            visible_menus = []
            
        groups = {}
        for m in visible_menus:
            grp = m.group_name or 'Utama'
            if grp not in groups:
                groups[grp] = []
            groups[grp].append(m)
            
        return dict(
            modular_menus=visible_menus,
            navigation_groups=groups,
            user_level=user_level
        )

    @app.route('/')
    def index():
        from modules.monitor.models import Server, ServerJump, ServerApp
        total_servers = Server.query.count()
        total_apps = ServerApp.query.count()
        total_jumps = ServerJump.query.count()
        servers = Server.query.order_by(Server.nama.asc()).all()
        apps = ServerApp.query.order_by(ServerApp.server_id.asc(), ServerApp.id.asc()).all()

        app_data = []
        for a in apps:
            app_data.append({
                'id': a.id,
                'kode': a.kode or f"APP-{a.id}",
                'nama': a.nama,
                'server_id': a.server_id,
                'server_nama': a.server.nama if a.server else '-',
                'server_host': a.server.host if a.server else '-',
                'app_ini_path': a.app_ini_path or '-',
                'app_log_path': a.app_log_path or '-',
                'default_download_path': a.default_download_path or (a.server.default_download_path if a.server else '-'),
                'service_type': a.service_type or 'systemd',
                'service_path': a.service_path or '-',
                'db_name': a.db_name or (a.server.db_name if a.server else '-'),
                'status': a.status
            })

        return render_template(
            'index.html',
            total_servers=total_servers,
            total_apps=total_apps,
            total_jumps=total_jumps,
            servers=servers,
            apps=app_data
        )

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('monitor.dashboard'))
            
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            remember = True if request.form.get('remember') else False

            user = User.query.filter_by(email=email).first()
            if user and bcrypt.check_password_hash(user.password, password):
                if not user.is_verified:
                    flash('Akun belum diverifikasi. Silakan periksa email Anda.', 'warning')
                    return redirect(url_for('login'))
                login_user(user, remember=remember)
                return redirect(url_for('monitor.dashboard'))
            else:
                flash('Login gagal. Periksa email dan password.', 'danger')
        
        return render_template('login.html')

    @app.route('/login/google')
    def google_login():
        if not os.getenv('GOOGLE_CLIENT_ID') or not hasattr(oauth, 'google'):
            flash('Google OAuth belum dikonfigurasi di server (.env).', 'warning')
            return redirect(url_for('login'))
        redirect_uri = url_for('google_authorize', _external=True)
        return oauth.google.authorize_redirect(redirect_uri)

    @app.route('/login/google/authorize')
    def google_authorize():
        if not os.getenv('GOOGLE_CLIENT_ID') or not hasattr(oauth, 'google'):
            flash('Google OAuth belum dikonfigurasi di server (.env).', 'warning')
            return redirect(url_for('login'))
        try:
            token = oauth.google.authorize_access_token()
            user_info = token.get('userinfo')
            
            user = User.query.filter_by(email=user_info['email']).first()
            if not user:
                user = User(email=user_info['email'], name=user_info['name'], is_verified=True)
                db.session.add(user)
                db.session.commit()
            
            login_user(user, remember=True)
            return redirect(url_for('monitor.dashboard'))
        except Exception as e:
            flash(f'Gagal login dengan Google: {e}', 'danger')
            return redirect(url_for('login'))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('monitor.dashboard'))

        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            confirm_password = request.form.get('confirm_password')

            if password != confirm_password:
                flash('Konfirmasi password tidak cocok.', 'danger')
                return redirect(url_for('register'))

            existing_user = User.query.filter_by(email=email).first()
            if existing_user:
                flash('Alamat email sudah terdaftar.', 'danger')
                return redirect(url_for('register'))

            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            new_user = User(name=name, email=email, password=hashed_pw, is_verified=True, level=1)
            db.session.add(new_user)
            db.session.commit()
            flash('Pendaftaran berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))

        return render_template('register.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @app.route('/home')
    @login_required
    def home():
        return redirect(url_for('monitor.dashboard'))

    @app.route('/admin/users')
    @login_required
    @permission_required('admin.users')
    def admin_users():
        users = User.query.order_by(User.id.asc()).all()
        return render_template('admin_users.html', users=users)

    @app.route('/admin/users/add', methods=['GET', 'POST'])
    @login_required
    @permission_required('admin.users')
    def admin_user_add():
        all_roles = Role.query.order_by(Role.name.asc()).all()
        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            is_admin = True if request.form.get('is_admin') else False
            level = int(request.form.get('level', 3 if is_admin else 1))
            role_ids = request.form.getlist('role_ids')
            if is_admin:
                level = 3
            
            existing = User.query.filter_by(email=email).first()
            if existing:
                flash('Email already exists.', 'danger')
                return redirect(url_for('admin_user_add'))
                
            hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
            new_user = User(name=name, email=email, password=hashed_pw, is_admin=is_admin, level=level, is_verified=True)
            if role_ids:
                new_user.roles = Role.query.filter(Role.id.in_([int(rid) for rid in role_ids])).all()
            db.session.add(new_user)
            db.session.commit()
            flash('User created successfully.', 'success')
            return redirect(url_for('admin_users'))
            
        return render_template('admin_user_form.html', user=None, all_roles=all_roles, selected_role_ids=set())

    @app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
    @login_required
    @permission_required('admin.users')
    def admin_user_edit(user_id):
        user = User.query.get_or_404(user_id)
        all_roles = Role.query.order_by(Role.name.asc()).all()
        if request.method == 'POST':
            user.name = request.form.get('name')
            user.email = request.form.get('email')
            user.is_admin = True if request.form.get('is_admin') else False
            level = int(request.form.get('level', 3 if user.is_admin else 1))
            role_ids = request.form.getlist('role_ids')
            if user.is_admin:
                level = 3
            user.level = level
            if role_ids:
                user.roles = Role.query.filter(Role.id.in_([int(rid) for rid in role_ids])).all()
            else:
                user.roles = []
            
            password = request.form.get('password')
            if password:
                user.password = bcrypt.generate_password_hash(password).decode('utf-8')
                
            db.session.commit()
            flash('User updated successfully.', 'success')
            return redirect(url_for('admin_users'))
            
        selected_role_ids = {r.id for r in user.roles}
        return render_template('admin_user_form.html', user=user, all_roles=all_roles, selected_role_ids=selected_role_ids)

    @app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
    @login_required
    @permission_required('admin.users')
    def admin_user_delete(user_id):
        user = User.query.get_or_404(user_id)
        if user.id == current_user.id:
            flash('You cannot delete your own account.', 'danger')
            return redirect(url_for('admin_users'))
            
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully.', 'success')
        return redirect(url_for('admin_users'))

    # --- Role & Permission Group Management Routes ---

    @app.route('/admin/roles')
    @login_required
    @permission_required('admin.roles')
    def admin_roles():
        roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
        return render_template('admin_roles.html', roles=roles)

    @app.route('/admin/roles/add', methods=['GET', 'POST'])
    @login_required
    @permission_required('admin.roles')
    def admin_role_add():
        all_perms = Permission.query.order_by(Permission.category.asc(), Permission.id.asc()).all()
        grouped_perms = {}
        for p in all_perms:
            cat = p.category or 'Umum'
            if cat not in grouped_perms:
                grouped_perms[cat] = []
            grouped_perms[cat].append(p)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            perm_ids = request.form.getlist('permission_ids')

            if not name:
                flash('Nama Role tidak boleh kosong.', 'danger')
                return redirect(url_for('admin_role_add'))

            existing = Role.query.filter_by(name=name).first()
            if existing:
                flash('Nama Role sudah digunakan.', 'danger')
                return redirect(url_for('admin_role_add'))

            new_role = Role(name=name, description=description, is_system=False)
            if perm_ids:
                selected_perms = Permission.query.filter(Permission.id.in_([int(pid) for pid in perm_ids])).all()
                new_role.permissions = selected_perms
            db.session.add(new_role)
            db.session.commit()
            flash(f'Role Group "{name}" berhasil dibuat.', 'success')
            return redirect(url_for('admin_roles'))

        return render_template('admin_role_form.html', role=None, grouped_perms=grouped_perms, selected_perm_ids=set())

    @app.route('/admin/roles/edit/<int:role_id>', methods=['GET', 'POST'])
    @login_required
    @permission_required('admin.roles')
    def admin_role_edit(role_id):
        role = Role.query.get_or_404(role_id)
        all_perms = Permission.query.order_by(Permission.category.asc(), Permission.id.asc()).all()
        grouped_perms = {}
        for p in all_perms:
            cat = p.category or 'Umum'
            if cat not in grouped_perms:
                grouped_perms[cat] = []
            grouped_perms[cat].append(p)

        if request.method == 'POST':
            if not role.is_system:
                name = request.form.get('name', '').strip()
                if name:
                    role.name = name
            role.description = request.form.get('description', '').strip()
            perm_ids = request.form.getlist('permission_ids')
            
            if perm_ids:
                selected_perms = Permission.query.filter(Permission.id.in_([int(pid) for pid in perm_ids])).all()
                role.permissions = selected_perms
            else:
                role.permissions = []
                
            db.session.commit()
            flash(f'Role Group "{role.name}" berhasil diperbarui.', 'success')
            return redirect(url_for('admin_roles'))

        selected_perm_ids = {p.id for p in role.permissions}
        return render_template('admin_role_form.html', role=role, grouped_perms=grouped_perms, selected_perm_ids=selected_perm_ids)

    @app.route('/admin/roles/delete/<int:role_id>', methods=['POST'])
    @login_required
    @permission_required('admin.roles')
    def admin_role_delete(role_id):
        role = Role.query.get_or_404(role_id)
        if role.is_system:
            flash('Role bawaan sistem tidak dapat dihapus.', 'danger')
            return redirect(url_for('admin_roles'))
            
        role_name = role.name
        db.session.delete(role)
        db.session.commit()
        flash(f'Role Group "{role_name}" berhasil dihapus.', 'success')
        return redirect(url_for('admin_roles'))

    @app.route('/admin/roles/sync', methods=['POST'])
    @login_required
    @permission_required('admin.roles')
    def admin_role_sync():
        sync_default_permissions_and_roles()
        sync_monitor_permissions_and_menus()
        flash('Daftar permission dan default role groups berhasil disinkronkan!', 'success')
        return redirect(url_for('admin_roles'))

    # --- Menu Management Routes ---

    @app.route('/admin/menus')
    @login_required
    @permission_required('admin.menus')
    def admin_menus():
        menus = AppMenu.query.order_by(AppMenu.order.asc(), AppMenu.id.asc()).all()
        groups = {}
        for m in menus:
            grp = m.group_name or 'Utama'
            if grp not in groups:
                groups[grp] = []
            groups[grp].append(m)
        return render_template('admin_menus.html', groups=groups, all_menus=menus)

    @app.route('/admin/menus/edit/<int:menu_id>', methods=['POST'])
    @login_required
    @permission_required('admin.menus')
    def admin_menu_edit(menu_id):
        menu = AppMenu.query.get_or_404(menu_id)
        menu.title = request.form.get('title', menu.title).strip()
        menu.icon = request.form.get('icon', menu.icon).strip()
        menu.group_name = request.form.get('group_name', menu.group_name).strip()
        menu.permission_code = request.form.get('permission_code', menu.permission_code).strip() or None
        try:
            menu.min_level = int(request.form.get('min_level', menu.min_level))
        except (ValueError, TypeError):
            pass
        try:
            menu.order = int(request.form.get('order', menu.order))
        except (ValueError, TypeError):
            pass
        db.session.commit()
        flash(f'Menu "{menu.title}" berhasil diperbarui.', 'success')
        return redirect(url_for('admin_menus'))

    @app.route('/admin/menus/toggle/<int:menu_id>', methods=['POST'])
    @login_required
    @permission_required('admin.menus')
    def admin_menu_toggle(menu_id):
        menu = AppMenu.query.get_or_404(menu_id)
        menu.is_active = not menu.is_active
        db.session.commit()
        status_text = "diaktifkan" if menu.is_active else "dinonaktifkan"
        flash(f'Menu "{menu.title}" telah {status_text}.', 'success')
        return redirect(url_for('admin_menus'))

    @app.route('/admin/menus/sync', methods=['POST'])
    @login_required
    @permission_required('admin.menus')
    def admin_menu_sync():
        sync_default_menus()
        sync_monitor_permissions_and_menus()
        flash('Seluruh menu navigasi berhasil disinkronkan dengan rute sistem!', 'success')
        return redirect(url_for('admin_menus'))
    
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template('500.html'), 500

    return app

app = create_app()

if __name__ == '__main__':
    import sys
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 8092))
    
    cli_args = [arg.lower() for arg in sys.argv[1:]]
    reload_enabled = ('reload' in cli_args or '--reload' in cli_args or os.getenv('RELOAD', '').lower() in ('1', 'true', 'yes'))
    
    if reload_enabled:
        print(f"[*] Starting Flask Server on http://{host}:{port} with AUTO-RELOAD enabled...")
        app.run(host=host, port=port, debug=True, use_reloader=True)
    else:
        print(f"[*] Starting Flask Server on http://{host}:{port} (NO AUTO-RELOAD)...")
        print("    Tip: Use 'runweb.bat reload' or 'python app.py reload' to enable live code reload.")
        app.run(host=host, port=port, debug=False, use_reloader=False)
