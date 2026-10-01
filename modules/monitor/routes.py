from __future__ import annotations
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from core.extensions import db
from core.decorators import permission_required
from modules.monitor.models import Server, ServerJump, ServerApp, ActivityLog, QueryHistory
from modules.monitor import ssh_helper, audit_logger

monitor_bp = Blueprint(
    'monitor',
    __name__,
    url_prefix='/monitor',
    template_folder='templates'
)


# =========================================================================
# DASHBOARD
# =========================================================================

@monitor_bp.route('/')
@monitor_bp.route('/dashboard')
@login_required
def dashboard():
    total_servers = Server.query.count()
    total_apps = ServerApp.query.count()
    total_jumps = ServerJump.query.count()
    recent_logs = ActivityLog.query.order_by(ActivityLog.id.desc()).limit(8).all()
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
        'monitor/dashboard.html',
        total_servers=total_servers,
        total_apps=total_apps,
        total_jumps=total_jumps,
        recent_logs=recent_logs,
        servers=servers,
        apps=app_data
    )


# =========================================================================
# SERVER MANAGEMENT
# =========================================================================

@monitor_bp.route('/servers')
@login_required
def server_list():
    servers = Server.query.order_by(Server.id.asc()).all()
    server_data = []
    for s in servers:
        jumps = [j.jump_server.nama for j in s.jumps if j.jump_server]
        server_data.append({
            'id': s.id,
            'kode': s.kode or f"SRV-{s.id}",
            'nama': s.nama,
            'host': s.host,
            'ssh_port': s.ssh_port or 22,
            'ssh_user': s.ssh_user or '-',
            'db_name': s.db_name or '-',
            'db_port': s.db_port or 5432,
            'apps_count': len(s.apps),
            'status': s.status,
            'jumps': jumps
        })
    return render_template('monitor/server_list.html', servers=server_data)


@monitor_bp.route('/servers/add', methods=['GET', 'POST'])
@login_required
@permission_required('admin.servers')
def server_add():
    if request.method == 'POST':
        nama = request.form.get('nama', '').strip()
        host = request.form.get('host', '').strip()
        if not nama or not host:
            flash('Nama Server dan Host/IP wajib diisi.', 'danger')
            return render_template('monitor/server_form.html', server=None)

        raw_host = host
        ssh_user = request.form.get('ssh_user', '').strip() or None
        if '@' in raw_host:
            u_p, h_p = raw_host.split('@', 1)
            host = h_p.strip()
            if not ssh_user:
                ssh_user = u_p.strip()

        server = Server(
            kode=request.form.get('kode', '').strip() or None,
            nama=nama,
            host=host,
            ssh_port=int(request.form.get('ssh_port', 22) or 22),
            ssh_user=ssh_user,
            ssh_password=request.form.get('ssh_password', '').strip() or None,
            ssh_key_path=request.form.get('ssh_key_path', '').strip() or None,
            db_host=request.form.get('db_host', '127.0.0.1').strip() or '127.0.0.1',
            db_port=int(request.form.get('db_port', 5432) or 5432),
            db_name=request.form.get('db_name', '').strip() or None,
            db_user=request.form.get('db_user', '').strip() or None,
            db_password=request.form.get('db_password', '').strip() or None,
            app_ini_path=request.form.get('app_ini_path', '').strip() or None,
            default_download_path=request.form.get('default_download_path', '').strip() or None,
            use_sudo=True if request.form.get('use_sudo') else False,
            su_user=request.form.get('su_user', '').strip() or None,
            status=1 if request.form.get('status') else 0
        )
        db.session.add(server)
        db.session.commit()

        audit_logger.log_activity(
            action_type='server_create',
            description=f"Menambahkan server baru: {server.nama} ({server.host})",
            server_id=server.id,
            target_title=server.nama,
            status='SUCCESS'
        )
        flash(f'Server "{server.nama}" berhasil disimpan.', 'success')
        return redirect(url_for('monitor.server_list'))

    return render_template('monitor/server_form.html', server=None)


@monitor_bp.route('/servers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@permission_required('admin.servers')
def server_edit(id):
    server = Server.query.get_or_404(id)
    if request.method == 'POST':
        server.nama = request.form.get('nama', '').strip() or server.nama
        server.kode = request.form.get('kode', '').strip() or None
        server.host = request.form.get('host', '').strip() or server.host
        server.ssh_port = int(request.form.get('ssh_port', 22) or 22)
        server.ssh_user = request.form.get('ssh_user', '').strip() or None
        
        pwd = request.form.get('ssh_password', '').strip()
        if pwd:
            server.ssh_password = pwd

        server.ssh_key_path = request.form.get('ssh_key_path', '').strip() or None
        server.db_host = request.form.get('db_host', '127.0.0.1').strip() or '127.0.0.1'
        server.db_port = int(request.form.get('db_port', 5432) or 5432)
        server.db_name = request.form.get('db_name', '').strip() or None
        server.db_user = request.form.get('db_user', '').strip() or None
        
        db_pwd = request.form.get('db_password', '').strip()
        if db_pwd:
            server.db_password = db_pwd

        server.app_ini_path = request.form.get('app_ini_path', '').strip() or None
        server.default_download_path = request.form.get('default_download_path', '').strip() or None
        server.use_sudo = True if request.form.get('use_sudo') else False
        server.su_user = request.form.get('su_user', '').strip() or None
        server.status = 1 if request.form.get('status') else 0

        db.session.commit()
        audit_logger.log_activity(
            action_type='server_edit',
            description=f"Mengubah profil server: {server.nama} ({server.host})",
            server_id=server.id,
            target_title=server.nama,
            status='SUCCESS'
        )
        flash(f'Perubahan server "{server.nama}" berhasil disimpan.', 'success')
        return redirect(url_for('monitor.server_list'))

    return render_template('monitor/server_form.html', server=server)


@monitor_bp.route('/servers/delete/<int:id>', methods=['POST'])
@login_required
@permission_required('admin.servers')
def server_delete(id):
    server = Server.query.get_or_404(id)
    nama = server.nama
    db.session.delete(server)
    db.session.commit()
    audit_logger.log_activity(
        action_type='server_delete',
        description=f"Menghapus server: {nama}",
        target_title=nama,
        status='SUCCESS'
    )
    flash(f'Server "{nama}" berhasil dihapus.', 'success')
    return redirect(url_for('monitor.server_list'))


@monitor_bp.route('/servers/act/check_status')
@login_required
def server_check_status():
    server_id = request.args.get('id')
    if not server_id:
        return jsonify({'success': False, 'message': 'ID Server tidak diberikan'}), 400
    success, msg = ssh_helper.test_ssh_connection(server_id)
    server = Server.query.get(server_id)
    if server:
        server.status = 1 if success else 0
        db.session.commit()
    return jsonify({
        'success': success,
        'server_id': int(server_id),
        'status': 1 if success else 0,
        'message': msg
    })


@monitor_bp.route('/servers/act/check_all')
@login_required
def server_check_all():
    servers = Server.query.order_by(Server.id.asc()).all()
    server_ids = [(s.id, s.nama) for s in servers]

    def _check_one(item):
        s_id, s_nama = item
        ok, msg = ssh_helper.test_ssh_connection(s_id)
        return {'server_id': s_id, 'server_nama': s_nama, 'success': ok, 'status': 1 if ok else 0, 'message': msg}

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_check_one, server_ids))

    for r in results:
        srv = Server.query.get(r['server_id'])
        if srv:
            srv.status = r['status']
    db.session.commit()

    return jsonify({'results': results})


# =========================================================================
# MULTI-APP MANAGEMENT (DUAL STATUS: SSH + SERVICE UP/DOWN)
# =========================================================================

@monitor_bp.route('/apps')
@login_required
def app_list():
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
    return render_template('monitor/app_list.html', apps=app_data)


@monitor_bp.route('/apps/add', methods=['GET', 'POST'])
@login_required
@permission_required('admin.apps')
def app_add():
    servers = Server.query.order_by(Server.nama.asc()).all()
    if request.method == 'POST':
        server_id = request.form.get('server_id')
        nama = request.form.get('nama', '').strip()
        if not server_id or not nama:
            flash('Server Induk dan Nama Aplikasi wajib diisi.', 'danger')
            return render_template('monitor/app_form.html', app=None, servers=servers)

        app_obj = ServerApp(
            server_id=int(server_id),
            kode=request.form.get('kode', '').strip() or None,
            nama=nama,
            app_ini_path=request.form.get('app_ini_path', '').strip() or None,
            app_log_path=request.form.get('app_log_path', '').strip() or None,
            service_type=request.form.get('service_type', 'systemd').strip() or 'systemd',
            service_path=request.form.get('service_path', '').strip() or None,
            default_download_path=request.form.get('default_download_path', '').strip() or None,
            use_sudo=True if request.form.get('use_sudo') else False,
            su_user=request.form.get('su_user', '').strip() or None,
            db_host=request.form.get('db_host', '127.0.0.1').strip() or '127.0.0.1',
            db_port=int(request.form.get('db_port', 5432) or 5432),
            db_name=request.form.get('db_name', '').strip() or None,
            db_user=request.form.get('db_user', '').strip() or None,
            db_password=request.form.get('db_password', '').strip() or None,
            status=1 if request.form.get('status') else 0
        )
        db.session.add(app_obj)
        db.session.commit()

        audit_logger.log_activity(
            action_type='app_create',
            description=f"Menambahkan aplikasi baru: {app_obj.nama} (Server ID: {app_obj.server_id})",
            server_id=app_obj.server_id,
            app_id=app_obj.id,
            target_title=app_obj.nama,
            status='SUCCESS'
        )
        flash(f'Aplikasi "{app_obj.nama}" berhasil disimpan.', 'success')
        return redirect(url_for('monitor.app_list'))

    return render_template('monitor/app_form.html', app=None, servers=servers)


@monitor_bp.route('/apps/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@permission_required('admin.apps')
def app_edit(id):
    app_obj = ServerApp.query.get_or_404(id)
    servers = Server.query.order_by(Server.nama.asc()).all()

    if request.method == 'POST':
        app_obj.server_id = int(request.form.get('server_id', app_obj.server_id))
        app_obj.kode = request.form.get('kode', '').strip() or None
        app_obj.nama = request.form.get('nama', '').strip() or app_obj.nama
        app_obj.app_ini_path = request.form.get('app_ini_path', '').strip() or None
        app_obj.app_log_path = request.form.get('app_log_path', '').strip() or None
        app_obj.service_type = request.form.get('service_type', 'systemd').strip() or 'systemd'
        app_obj.service_path = request.form.get('service_path', '').strip() or None
        app_obj.default_download_path = request.form.get('default_download_path', '').strip() or None
        app_obj.use_sudo = True if request.form.get('use_sudo') else False
        app_obj.su_user = request.form.get('su_user', '').strip() or None
        app_obj.db_host = request.form.get('db_host', '127.0.0.1').strip() or '127.0.0.1'
        app_obj.db_port = int(request.form.get('db_port', 5432) or 5432)
        app_obj.db_name = request.form.get('db_name', '').strip() or None
        app_obj.db_user = request.form.get('db_user', '').strip() or None

        db_pwd = request.form.get('db_password', '').strip()
        if db_pwd:
            app_obj.db_password = db_pwd

        app_obj.status = 1 if request.form.get('status') else 0
        db.session.commit()

        audit_logger.log_activity(
            action_type='app_edit',
            description=f"Mengubah konfigurasi aplikasi: {app_obj.nama}",
            server_id=app_obj.server_id,
            app_id=app_obj.id,
            target_title=app_obj.nama,
            status='SUCCESS'
        )
        flash(f'Perubahan aplikasi "{app_obj.nama}" berhasil disimpan.', 'success')
        return redirect(url_for('monitor.app_list'))

    return render_template('monitor/app_form.html', app=app_obj, servers=servers)


@monitor_bp.route('/apps/delete/<int:id>', methods=['POST'])
@login_required
@permission_required('admin.apps')
def app_delete(id):
    app_obj = ServerApp.query.get_or_404(id)
    nama = app_obj.nama
    db.session.delete(app_obj)
    db.session.commit()
    audit_logger.log_activity(
        action_type='app_delete',
        description=f"Menghapus aplikasi: {nama}",
        target_title=nama,
        status='SUCCESS'
    )
    flash(f'Aplikasi "{nama}" berhasil dihapus.', 'success')
    return redirect(url_for('monitor.app_list'))


@monitor_bp.route('/apps/act/check_status')
@login_required
def app_check_status():
    app_id = request.args.get('id')
    if not app_id:
        return jsonify({'success': False, 'message': 'ID Aplikasi tidak diberikan'}), 400

    app_obj = ServerApp.query.get(app_id)
    if not app_obj:
        return jsonify({'success': False, 'message': 'Aplikasi tidak ditemukan'}), 404

    res = ssh_helper.check_remote_service_status(app_id=app_id)
    ssh_ok = res.get('ssh_success', False)
    app_obj.status = 1 if ssh_ok else 0
    db.session.commit()

    return jsonify({
        'success': res.get('success', False),
        'app_id': int(app_id),
        'app_nama': app_obj.nama,
        'status': 1 if ssh_ok else 0,
        'ssh_success': ssh_ok,
        'service_name': res.get('service_name', '-'),
        'service_type': res.get('service_type', 'systemd'),
        'service_status': res.get('service_status', 'unknown'),
        'service_is_up': res.get('is_active', False),
        'service_status_text': res.get('status_text', '-'),
        'service_output': res.get('output', ''),
        'elapsed_ms': res.get('elapsed_ms', 0),
        'message': f"SSH: {'Aktif' if ssh_ok else 'Gagal'} | Service: {res.get('status_text', '-')}"
    })


@monitor_bp.route('/apps/act/check_all')
@login_required
def app_check_all():
    apps = ServerApp.query.order_by(ServerApp.id.asc()).all()
    app_ids = [(a.id, a.nama, a.server_id) for a in apps]

    def _check_one(item):
        a_id, a_nama, s_id = item
        res = ssh_helper.check_remote_service_status(app_id=a_id)
        return {
            'app_id': a_id,
            'app_nama': a_nama,
            'server_id': s_id,
            'status': 1 if res.get('ssh_success') else 0,
            'success': res.get('ssh_success', False),
            'ssh_success': res.get('ssh_success', False),
            'service_name': res.get('service_name', '-'),
            'service_type': res.get('service_type', 'systemd'),
            'service_status': res.get('service_status', 'unknown'),
            'service_is_up': res.get('is_active', False),
            'service_status_text': res.get('status_text', '-'),
            'service_output': res.get('output', ''),
            'elapsed_ms': res.get('elapsed_ms', 0)
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_check_one, app_ids))

    for r in results:
        a_obj = ServerApp.query.get(r['app_id'])
        if a_obj:
            a_obj.status = r['status']
    db.session.commit()

    return jsonify({'results': results})


@monitor_bp.route('/apps/act/restart_service')
@login_required
def app_restart_service():
    app_id = request.args.get('id')
    service_path = request.args.get('service_path')
    service_type = request.args.get('service_type')
    if not app_id:
        return jsonify({'success': False, 'message': 'ID Aplikasi tidak diberikan'}), 400

    try:
        res = ssh_helper.restart_remote_service(app_id=app_id, service_path=service_path, service_type=service_type)
        audit_logger.log_activity(
            action_type='service_restart',
            description=f"Restart remote service: {res['service_name']} (Status: {'SUCCESS' if res['success'] else 'FAILED'})",
            app_id=int(app_id),
            status='SUCCESS' if res['success'] else 'FAILED',
            details=res
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'output': str(e)})


@monitor_bp.route('/apps/act/dump_db', methods=['GET', 'POST'])
@login_required
def app_dump_db():
    app_id = request.args.get('id') or request.form.get('id')
    server_id = request.args.get('server_id') or request.form.get('server_id')
    if not app_id and not server_id:
        return jsonify({'success': False, 'message': 'ID Aplikasi atau Server tidak diberikan'}), 400
    try:
        meta = ssh_helper.dump_database(server_id=server_id, app_id=app_id, return_meta=True)
        audit_logger.log_activity(
            action_type='db_dump',
            description=f"Dump database {meta['dbname']} ({meta['file_size']}) via {meta['method']}",
            app_id=int(app_id) if app_id else None,
            server_id=int(server_id) if server_id else None,
            status='SUCCESS',
            details=meta
        )
        if request.args.get('download') == '1':
            return send_file(meta['local_file'], as_attachment=True, download_name=meta['filename'])
        meta['download_url'] = url_for('monitor.app_download_dump_file', filename=meta['filename'])
        return jsonify(meta)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@monitor_bp.route('/apps/act/download_dump/<filename>')
@login_required
def app_download_dump_file(filename):
    tmp_dir = ssh_helper.get_monitor_tmp_dir()
    filepath = os.path.join(tmp_dir, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name=filename)
    flash(f"File dump {filename} tidak ditemukan di penyimpanan sementara.", 'warning')
    return redirect(url_for('monitor.app_list'))


@monitor_bp.route('/apps/act/read_log')
@login_required
def app_read_log():
    app_id = request.args.get('id')
    server_id = request.args.get('server_id')
    log_path = request.args.get('path')
    lines = int(request.args.get('lines', 1000) or 1000)
    if not app_id and not server_id:
        return jsonify({'success': False, 'message': 'ID Aplikasi atau Server tidak diberikan'}), 400
    try:
        content = ssh_helper.read_remote_log(server_id=server_id, app_id=app_id, log_path=log_path, lines=lines)
        return jsonify({'success': True, 'content': content, 'lines': lines})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@monitor_bp.route('/apps/act/download_log')
@login_required
def app_download_log():
    app_id = request.args.get('id')
    server_id = request.args.get('server_id')
    log_path = request.args.get('path')
    try:
        local_f = ssh_helper.download_remote_log_file(server_id=server_id, app_id=app_id, log_path=log_path)
        return send_file(local_f, as_attachment=True, download_name=os.path.basename(local_f))
    except Exception as e:
        flash(f"Gagal mengunduh log: {e}", 'danger')
        return redirect(url_for('monitor.app_list'))


@monitor_bp.route('/apps/act/git_pull', methods=['GET', 'POST'])
@login_required
def app_git_pull():
    app_id = request.args.get('id') or request.form.get('id')
    server_id = request.args.get('server_id') or request.form.get('server_id')
    git_cmd = request.args.get('git_cmd') or request.form.get('git_cmd') or 'git pull'
    target_path = request.args.get('target_path') or request.form.get('target_path')
    git_user = request.form.get('git_username')
    git_pass = request.form.get('git_password')
    enable_chmod = True if (request.form.get('enable_chmod') == '1' or request.args.get('enable_chmod') == '1') else False

    try:
        res = ssh_helper.execute_remote_git_command(
            server_id=server_id,
            app_id=app_id,
            target_path=target_path,
            git_cmd=git_cmd,
            git_username=git_user,
            git_password=git_pass,
            enable_chmod=enable_chmod
        )
        audit_logger.log_activity(
            action_type='git_pull',
            description=f"Eksekusi Git ({git_cmd}) pada {res.get('path')}",
            app_id=int(app_id) if app_id else None,
            server_id=int(server_id) if server_id else None,
            status='SUCCESS' if res.get('success') else 'FAILED',
            details=res
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'output': str(e)}), 500


@monitor_bp.route('/apps/act/terminal_exec', methods=['POST'])
@login_required
def app_terminal_exec():
    app_id = request.form.get('app_id')
    server_id = request.form.get('server_id')
    command = request.form.get('command')
    workdir = request.form.get('workdir')
    use_sudo = True if request.form.get('use_sudo') == '1' else False
    su_user = request.form.get('su_user')

    try:
        res = ssh_helper.execute_remote_terminal_command(
            server_id=server_id,
            app_id=app_id,
            command=command,
            workdir=workdir,
            use_sudo=use_sudo,
            su_user=su_user
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'output': str(e), 'exit_code': 1, 'workdir': workdir or '/'}), 500


@monitor_bp.route('/apps/act/user_access')
@login_required
def app_user_access():
    app_id = request.args.get('id')
    server_id = request.args.get('server_id')
    filter_pegawai = request.args.get('filter_pegawai', 'all')
    try:
        user_records, srv, app_obj = ssh_helper.get_server_user_access(server_id=server_id, app_id=app_id, filter_pegawai=filter_pegawai)
        return jsonify({
            'success': True,
            'server_nama': srv.nama if srv else '-',
            'app_nama': app_obj.nama if app_obj else '-',
            'total_users': len(user_records),
            'users': user_records
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@monitor_bp.route('/apps/act/file_manager')
@login_required
def app_file_manager():
    app_id = request.args.get('id')
    server_id = request.args.get('server_id')
    target_path = request.args.get('path')
    action = request.args.get('action', 'list')

    if action == 'download':
        try:
            local_tar = ssh_helper.download_remote_path(server_id=server_id, app_id=app_id, remote_path=target_path)
            return send_file(local_tar, as_attachment=True, download_name=os.path.basename(local_tar))
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    try:
        res = ssh_helper.file_manager_list(server_id=server_id, app_id=app_id, target_path=target_path)
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



# =========================================================================
# JUMP HOST MANAGEMENT
# =========================================================================

@monitor_bp.route('/jumps')
@login_required
def jump_list():
    jumps = ServerJump.query.order_by(ServerJump.server_id.asc(), ServerJump.urutan.asc()).all()
    return render_template('monitor/jump_list.html', jumps=jumps)


@monitor_bp.route('/jumps/add', methods=['GET', 'POST'])
@login_required
@permission_required('admin.jumps')
def jump_add():
    servers = Server.query.order_by(Server.nama.asc()).all()
    if request.method == 'POST':
        server_id = request.form.get('server_id')
        jump_server_id = request.form.get('jump_server_id')
        urutan = int(request.form.get('urutan', 1) or 1)

        if not server_id or not jump_server_id:
            flash('Server Target dan Jump Server wajib dipilih.', 'danger')
            return render_template('monitor/jump_form.html', servers=servers)

        if server_id == jump_server_id:
            flash('Server Target dan Jump Server tidak boleh sama.', 'danger')
            return render_template('monitor/jump_form.html', servers=servers)

        existing = ServerJump.query.filter_by(server_id=server_id, jump_server_id=jump_server_id).first()
        if existing:
            flash('Rute Jump Host ini sudah pernah didaftarkan.', 'warning')
            return render_template('monitor/jump_form.html', servers=servers)

        sj = ServerJump(server_id=int(server_id), jump_server_id=int(jump_server_id), urutan=urutan)
        db.session.add(sj)
        db.session.commit()

        audit_logger.log_activity(
            action_type='jump_create',
            description=f"Menambahkan rute Jump Host: Server #{server_id} -> Jump #{jump_server_id} (Urutan: {urutan})",
            server_id=int(server_id),
            status='SUCCESS'
        )
        flash('Rute Jump Host berhasil disimpan.', 'success')
        return redirect(url_for('monitor.jump_list'))

    return render_template('monitor/jump_form.html', servers=servers)


@monitor_bp.route('/jumps/delete/<int:id>', methods=['POST'])
@login_required
@permission_required('admin.jumps')
def jump_delete(id):
    sj = ServerJump.query.get_or_404(id)
    db.session.delete(sj)
    db.session.commit()
    flash('Rute Jump Host berhasil dihapus.', 'success')
    return redirect(url_for('monitor.jump_list'))


# =========================================================================
# SQL QUERY CONSOLE (FULL AJAX & QUERY HISTORY)
# =========================================================================

@monitor_bp.route('/query', methods=['GET', 'POST'])
@login_required
@permission_required('admin.query')
def sql_query():
    server_id = request.args.get('server_id') or request.args.get('id')
    app_id = request.args.get('app_id')

    servers = Server.query.order_by(Server.nama.asc()).all()
    apps = ServerApp.query.order_by(ServerApp.nama.asc()).all()

    target_title = 'SQL Query Console'
    target_obj = None
    if app_id:
        target_obj = ServerApp.query.get(app_id)
        if target_obj:
            target_title = f"SQL Console &bull; {target_obj.nama}"
    elif server_id:
        target_obj = Server.query.get(server_id)
        if target_obj:
            target_title = f"SQL Console &bull; {target_obj.nama}"

    # Handle AJAX Query Execution
    if request.method == 'POST' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        query_text = request.form.get('query_text', '').strip()
        limit = int(request.form.get('limit', 100) or 100)
        s_id = request.form.get('server_id') or server_id
        a_id = request.form.get('app_id') or app_id

        if not query_text:
            return jsonify({'success': False, 'error': 'Query SQL tidak boleh kosong.'}), 400

        try:
            res = ssh_helper.execute_remote_sql_query(server_id=s_id, app_id=a_id, query_text=query_text, limit=limit)
            if res.get('success'):
                audit_logger.record_query_history(
                    query_text=query_text,
                    elapsed_ms=int(res.get('elapsed_ms', 0)),
                    row_count=int(res.get('row_count', 0)),
                    status='SUCCESS',
                    server_id=int(s_id) if s_id else None,
                    app_id=int(a_id) if a_id else None
                )
            else:
                audit_logger.record_query_history(
                    query_text=query_text,
                    elapsed_ms=int(res.get('elapsed_ms', 0)),
                    row_count=0,
                    status='ERROR',
                    error_message=res.get('error'),
                    server_id=int(s_id) if s_id else None,
                    app_id=int(a_id) if a_id else None
                )
            res['recent_history'] = audit_logger.get_recent_query_history(server_id=s_id, app_id=a_id, limit=10)
            return jsonify(res)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    recent_history = audit_logger.get_recent_query_history(server_id=server_id, app_id=app_id, limit=15)
    return render_template(
        'monitor/query.html',
        target_title=target_title,
        target_obj=target_obj,
        server_id=server_id,
        app_id=app_id,
        servers=servers,
        apps=apps,
        recent_history=recent_history
    )


# =========================================================================
# AUDIT ACTIVITY LOGS
# =========================================================================

@monitor_bp.route('/logs')
@login_required
@permission_required('admin.logs')
def activity_logs():
    q = ActivityLog.query
    action_type = request.args.get('action')
    status = request.args.get('status')
    if action_type:
        q = q.filter(ActivityLog.action_type == action_type)
    if status:
        q = q.filter(ActivityLog.status == status)

    logs = q.order_by(ActivityLog.id.desc()).limit(200).all()
    return render_template('monitor/activity_logs.html', logs=logs)
