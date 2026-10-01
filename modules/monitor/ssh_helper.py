from __future__ import annotations
import os
import time
import re
import socket
import select
import threading
import logging
import shutil
import subprocess
import uuid
import base64
import glob
import gzip
import json
import tarfile
import tempfile
import io
import paramiko
from core.extensions import db
from modules.monitor.models import Server, ServerJump, ServerApp
from modules.monitor import db_dumper, user_access_helper

log = logging.getLogger(__name__)

def get_monitor_tmp_dir():
    tmp = os.path.join(tempfile.gettempdir(), 'flask_monitor')
    os.makedirs(tmp, exist_ok=True)
    return tmp


def get_jump_path(target_server_id):
    """
    Mendapatkan jalur rantai (hop) dari server-server jump yang harus dilalui
    untuk mencapai target_server_id. Mendukung rantai multi-hop otomatis.
    """
    direct_jumps = ServerJump.query.filter_by(server_id=target_server_id).order_by(ServerJump.urutan).all()
    if not direct_jumps:
        return []
    
    if len(direct_jumps) > 1:
        return direct_jumps

    resolved_jumps = []
    visited = set([target_server_id])

    def _collect_upstream(current_server_id):
        curr_jumps = ServerJump.query.filter_by(server_id=current_server_id).order_by(ServerJump.urutan).all()
        for j in curr_jumps:
            if j.jump_server_id not in visited:
                visited.add(j.jump_server_id)
                _collect_upstream(j.jump_server_id)
                resolved_jumps.append(j)

    _collect_upstream(target_server_id)
    return resolved_jumps or direct_jumps


def _parse_host_and_user(server):
    raw_host = (server.host or "").strip()
    raw_user = (server.ssh_user or "").strip()
    if "@" in raw_host:
        u_part, h_part = raw_host.split("@", 1)
        clean_host = h_part.strip()
        clean_user = raw_user or u_part.strip()
    else:
        clean_host = raw_host
        clean_user = raw_user
    return clean_host, clean_user


def create_ssh_client(server, sock=None, timeout=15):
    """Membuat instance paramiko.SSHClient untuk server target atau jump server."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    clean_host, clean_user = _parse_host_and_user(server)
    
    connect_kwargs = {
        "hostname": clean_host,
        "port": server.ssh_port or 22,
        "username": clean_user,
        "timeout": timeout,
        "banner_timeout": 30,
        "auth_timeout": 30,
        "look_for_keys": False,
        "allow_agent": False,
    }
    
    if server.ssh_password and str(server.ssh_password).strip():
        connect_kwargs["password"] = server.ssh_password
    elif server.ssh_key_path and os.path.exists(server.ssh_key_path):
        connect_kwargs["key_filename"] = server.ssh_key_path
    else:
        raise ValueError(f"Password SSH atau file SSH Key untuk server [{server.nama}] belum diisi.")
        
    if sock:
        connect_kwargs["sock"] = sock
        
    log.info(f"Connecting to SSH: {clean_host}:{server.ssh_port} as {clean_user}")
    client.connect(**connect_kwargs)
    return client


def _resolve_target_context(server_id=None, app_id=None):
    """Menentukan target server dan konfigurasi database/aplikasi."""
    if app_id:
        app = ServerApp.query.get(app_id)
        if not app:
            raise ValueError(f"Aplikasi dengan ID {app_id} tidak ditemukan.")
        server = app.server
        db_config = {
            'host': app.db_host or server.db_host or '127.0.0.1',
            'port': app.db_port or server.db_port or 5432,
            'name': app.db_name or server.db_name or None,
            'user': app.db_user or server.db_user or None,
            'password': app.db_password if (app.db_password is not None and app.db_password != '') else (server.db_password or None),
            'method': app.dump_method or server.dump_method or 'script'
        }
        app_config = {
            'kode': f"{server.kode or 'SRV'}_{app.kode or 'APP'}",
            'nama': f"{server.nama} - {app.nama}",
            'ini_path': app.app_ini_path or server.app_ini_path,
            'log_path': app.app_log_path,
            'download_path': app.default_download_path or server.default_download_path,
            'service_type': getattr(app, 'service_type', 'systemd') or 'systemd',
            'service_path': app.service_path,
            'use_sudo': app.use_sudo if app.use_sudo is not None else server.use_sudo,
            'su_user': app.su_user or server.su_user
        }
        return server, app, db_config, app_config
    elif server_id:
        server = Server.query.get(server_id)
        if not server:
            raise ValueError(f"Server dengan ID {server_id} tidak ditemukan.")
        db_config = {
            'host': server.db_host or '127.0.0.1',
            'port': server.db_port or 5432,
            'name': server.db_name or None,
            'user': server.db_user or None,
            'password': server.db_password if (server.db_password is not None and server.db_password != '') else None,
            'method': server.dump_method or 'script'
        }
        app_config = {
            'kode': server.kode or 'SRV',
            'nama': server.nama,
            'ini_path': server.app_ini_path,
            'log_path': None,
            'download_path': server.default_download_path,
            'service_type': 'systemd',
            'service_path': None,
            'use_sudo': server.use_sudo,
            'su_user': server.su_user
        }
        return server, None, db_config, app_config
    else:
        raise ValueError("Harus menyertakan server_id atau app_id.")


def _build_jump_chain(target_server_id):
    """Membangun koneksi multi-hop melalui jump server hingga ke server target."""
    target_server = Server.query.get(target_server_id)
    if not target_server:
        raise ValueError(f"Server {target_server_id} tidak ditemukan.")

    jump_routes = get_jump_path(target_server_id)
    clients = []
    current_sock = None

    for jump in jump_routes:
        jump_srv = jump.jump_server
        if not jump_srv:
            continue
        jump_client = create_ssh_client(jump_srv, sock=current_sock)
        clients.append(jump_client)
        
        target_host_clean, _ = _parse_host_and_user(target_server)
        transport = jump_client.get_transport()
        current_sock = transport.open_channel(
            "direct-tcpip",
            (target_host_clean, target_server.ssh_port or 22),
            ("127.0.0.1", 0)
        )

    target_client = create_ssh_client(target_server, sock=current_sock)
    clients.append(target_client)
    return target_client, clients, current_sock


def test_ssh_connection(server_id):
    """Menguji otentikasi SSH ke server target secara langsung atau via Jump Host."""
    server = Server.query.get(server_id)
    if not server:
        return False, "Server tidak ditemukan di database."

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(server.id)
        stdin, stdout, stderr = target_client.exec_command("echo 'MANDB_SSH_OK'", timeout=10)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        if "MANDB_SSH_OK" in out:
            jump_count = len(clients) - 1
            if jump_count > 0:
                return True, f"Koneksi SSH berhasil terhubung melalui {jump_count} Jump Host."
            return True, "Koneksi SSH langsung (direct) berhasil."
        return False, "Koneksi SSH terhubung namun gagal mengeksekusi perintah tes."
    except Exception as e:
        return False, f"Otentikasi SSH Gagal: {str(e)}"
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


# ==============================================================================
# SERVICE STATUS & RESTART
# ==============================================================================

def check_remote_service_status(server_id=None, app_id=None, service_path=None, service_type=None):
    """Memeriksa status service (Systemd atau Supervisor) di server remote."""
    t_start = time.perf_counter()
    try:
        target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    except Exception as e:
        return {
            'success': False,
            'ssh_success': False,
            'service_name': '-',
            'service_type': service_type or 'systemd',
            'service_status': 'error',
            'is_active': False,
            'status_text': 'Error Context',
            'output': str(e),
            'elapsed_ms': 0
        }

    raw_service = (service_path or app_config.get('service_path') or '').strip()
    eff_type = (service_type or app_config.get('service_type') or '').strip().lower()

    if not eff_type:
        if 'supervisor' in raw_service.lower():
            eff_type = 'supervisor'
        else:
            eff_type = 'systemd'

    if not raw_service:
        if eff_type == 'supervisor':
            raw_service = 'all'
        else:
            ssh_ok, ssh_msg = test_ssh_connection(server_id=target_server.id)
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            return {
                'success': ssh_ok,
                'ssh_success': ssh_ok,
                'service_name': '-',
                'service_type': eff_type,
                'service_status': 'unconfigured' if ssh_ok else 'ssh_failed',
                'is_active': False,
                'status_text': 'Belum Diatur' if ssh_ok else 'SSH Gagal',
                'output': 'Nama service systemd belum dikonfigurasi pada profil aplikasi ini.' if ssh_ok else f'Koneksi SSH gagal: {ssh_msg}',
                'elapsed_ms': elapsed_ms,
                'server_host': target_server.host,
                'server_nama': target_server.nama
            }

    raw_lower = raw_service.lower()
    is_supervisor = False
    if eff_type == 'supervisor' or 'supervisor' in raw_lower:
        is_supervisor = True
        if raw_service.startswith('supervisor:'):
            target_prog = raw_service.split(':', 1)[1].strip() or 'all'
        elif raw_service.strip() in ['supervisorctl', 'supervisor', 'all', '']:
            target_prog = 'all'
        else:
            target_prog = raw_service.strip()
        display_service = f"supervisorctl: {target_prog}"
        check_cmd = f"supervisorctl status {target_prog}"
    elif 'systemctl' in raw_lower:
        parts = raw_service.split()
        svc = parts[-1]
        if not svc.endswith('.service') and '.' not in svc:
            svc = f"{svc}.service"
        display_service = svc
        check_cmd = f"systemctl is-active {svc}; echo '---'; systemctl status {svc} --no-pager -l -n 10"
    else:
        if ' ' not in raw_service and not raw_service.startswith('/'):
            clean_svc = raw_service
            if not clean_svc.endswith('.service') and '.' not in clean_svc:
                clean_svc = f"{clean_svc}.service"
            display_service = clean_svc
        elif raw_service.startswith('/etc/systemd/system/') or raw_service.endswith('.service'):
            clean_svc = os.path.basename(raw_service).strip()
            if not clean_svc.endswith('.service') and '.' not in clean_svc:
                clean_svc = f"{clean_svc}.service"
            display_service = clean_svc
        else:
            display_service = raw_service
        check_cmd = f"systemctl is-active {display_service}; echo '---'; systemctl status {display_service} --no-pager -l -n 10"

    check_script = f"""export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
{check_cmd}
"""
    jumps = get_jump_path(target_server.id)
    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        try:
            res = subprocess.run(check_script, shell=True, capture_output=True, text=True, timeout=15)
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            out = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()

            if is_supervisor:
                if "RUNNING" in out or "STARTING" in out:
                    service_status, is_active, status_text = "active", True, "UP (Running)"
                elif "STOPPED" in out:
                    service_status, is_active, status_text = "inactive", False, "DOWN (Stopped)"
                elif "FATAL" in out or "BACKOFF" in out or "EXITED" in out:
                    service_status, is_active, status_text = "failed", False, "FAILED"
                elif "no such process" in out.lower() or "not found" in out.lower():
                    service_status, is_active, status_text = "not_found", False, "Not Found"
                else:
                    is_active = res.returncode == 0
                    service_status, status_text = "active" if is_active else "inactive", "UP" if is_active else "DOWN"
            else:
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                first_line = lines[0].lower() if lines else ""
                if first_line == "active" or "Active: active (running)" in out or "Active: active" in out:
                    service_status, is_active, status_text = "active", True, "UP (Running)"
                elif first_line == "inactive" or "Active: inactive" in out:
                    service_status, is_active, status_text = "inactive", False, "DOWN (Inactive)"
                elif first_line == "failed" or "Active: failed" in out:
                    service_status, is_active, status_text = "failed", False, "FAILED"
                elif first_line in ["activating", "reloading"]:
                    service_status, is_active, status_text = "activating", True, "STARTING"
                elif "could not be found" in out.lower() or "not-found" in out.lower():
                    service_status, is_active, status_text = "not_found", False, "Not Found"
                else:
                    is_active = "active (running)" in out
                    service_status, status_text = "active" if is_active else "inactive", "UP (Running)" if is_active else "DOWN"

            return {
                'success': True,
                'ssh_success': True,
                'service_name': display_service,
                'service_type': eff_type,
                'service_status': service_status,
                'is_active': is_active,
                'status_text': status_text,
                'output': out,
                'elapsed_ms': elapsed_ms,
                'server_host': target_server.host,
                'server_nama': target_server.nama
            }
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            return {
                'success': False,
                'ssh_success': True,
                'service_name': display_service,
                'service_type': eff_type,
                'service_status': 'error',
                'is_active': False,
                'status_text': 'Error',
                'output': str(e),
                'elapsed_ms': elapsed_ms
            }

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        b64_script = base64.b64encode(check_script.encode('utf-8')).decode('ascii')
        cmd_exec = f"bash -c 'echo \"{b64_script}\" | base64 -d | bash'"

        stdin, stdout, stderr = target_client.exec_command(cmd_exec, timeout=20)
        raw_out = stdout.read().decode('utf-8', errors='ignore')
        raw_err = stderr.read().decode('utf-8', errors='ignore')
        exit_code = stdout.channel.recv_exit_status()

        combined_output = (raw_out + ("\n" + raw_err if raw_err else "")).strip()
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)

        if is_supervisor:
            if "RUNNING" in combined_output or "STARTING" in combined_output:
                service_status, is_active, status_text = "active", True, "UP (Running)"
            elif "STOPPED" in combined_output:
                service_status, is_active, status_text = "inactive", False, "DOWN (Stopped)"
            elif "FATAL" in combined_output or "BACKOFF" in combined_output or "EXITED" in combined_output:
                service_status, is_active, status_text = "failed", False, "FAILED"
            elif "no such process" in combined_output.lower() or "not found" in combined_output.lower():
                service_status, is_active, status_text = "not_found", False, "Not Found"
            else:
                is_active = exit_code == 0
                service_status, status_text = "active" if is_active else "inactive", "UP" if is_active else "DOWN"
        else:
            lines = [l.strip() for l in combined_output.splitlines() if l.strip()]
            first_line = lines[0].lower() if lines else ""
            if first_line == "active" or "Active: active (running)" in combined_output or "Active: active" in combined_output:
                service_status, is_active, status_text = "active", True, "UP (Running)"
            elif first_line == "inactive" or "Active: inactive" in combined_output:
                service_status, is_active, status_text = "inactive", False, "DOWN (Inactive)"
            elif first_line == "failed" or "Active: failed" in combined_output:
                service_status, is_active, status_text = "failed", False, "FAILED"
            elif first_line in ["activating", "reloading"]:
                service_status, is_active, status_text = "activating", True, "STARTING"
            elif "could not be found" in combined_output.lower() or "not-found" in combined_output.lower():
                service_status, is_active, status_text = "not_found", False, "Not Found"
            else:
                is_active = "active (running)" in combined_output
                service_status, status_text = "active" if is_active else "inactive", "UP (Running)" if is_active else "DOWN"

        return {
            'success': True,
            'ssh_success': True,
            'service_name': display_service,
            'service_type': eff_type,
            'service_status': service_status,
            'is_active': is_active,
            'status_text': status_text,
            'output': combined_output,
            'elapsed_ms': elapsed_ms,
            'server_host': target_server.host,
            'server_nama': target_server.nama
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            'success': False,
            'ssh_success': False,
            'service_name': display_service if 'display_service' in locals() else '-',
            'service_type': eff_type if 'eff_type' in locals() else 'systemd',
            'service_status': 'ssh_failed',
            'is_active': False,
            'status_text': 'SSH Gagal',
            'output': f"Gagal menghubungkan SSH: {str(e)}",
            'elapsed_ms': elapsed_ms
        }
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def restart_remote_service(server_id=None, app_id=None, service_path=None, service_type=None):
    """Merestart service aplikasi (Systemd atau Supervisor) di server remote via sudo."""
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    raw_service = (service_path or app_config.get('service_path') or '').strip()
    eff_type = (service_type or app_config.get('service_type') or '').strip().lower()

    if not eff_type:
        eff_type = 'supervisor' if 'supervisor' in raw_service.lower() else 'systemd'

    if not raw_service:
        if eff_type == 'supervisor':
            raw_service = 'all'
        else:
            raise ValueError(f"Nama service systemd belum dikonfigurasi untuk '{app_config.get('nama')}'.")

    sudo_pwd = target_server.ssh_password or ""
    raw_lower = raw_service.lower()

    if eff_type == 'supervisor' or 'supervisor' in raw_lower:
        target_prog = raw_service.split(':', 1)[1].strip() if raw_service.startswith('supervisor:') else (raw_service.strip() or 'all')
        restart_cmd = f"supervisorctl restart {target_prog}"
        status_cmd = f"supervisorctl status {target_prog}"
        display_service = f"supervisorctl: {target_prog}"
        is_supervisor = True
    else:
        clean_svc = raw_service
        if not clean_svc.endswith('.service') and '.' not in clean_svc and not clean_svc.startswith('/'):
            clean_svc = f"{clean_svc}.service"
        restart_cmd = f"systemctl restart {clean_svc}"
        status_cmd = f"systemctl status {clean_svc} --no-pager -l -n 15"
        display_service = clean_svc
        is_supervisor = False

    restart_script = f"""export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
echo "=== RESTARTING SERVICE ({display_service}) ==="
{restart_cmd}
RC=$?
echo "=== SERVICE STATUS ({display_service}) ==="
{status_cmd}
exit $RC
"""
    t_start = time.perf_counter()
    jumps = get_jump_path(target_server.id)

    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        res = subprocess.run(restart_script, shell=True, capture_output=True, text=True, timeout=30)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        out = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        is_active = ("RUNNING" in out) if is_supervisor else ("active (running)" in out or res.returncode == 0)
        return {
            'success': res.returncode == 0,
            'service_name': display_service,
            'is_active': is_active,
            'output': out,
            'elapsed_ms': elapsed_ms
        }

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        b64_script = base64.b64encode(restart_script.encode('utf-8')).decode('ascii')
        cmd_exec = f"sudo -S -p '' -H bash -c 'echo \"{b64_script}\" | base64 -d | bash'"

        stdin, stdout, stderr = target_client.exec_command(cmd_exec, timeout=30)
        if sudo_pwd:
            try:
                stdin.write(f"{sudo_pwd}\n")
                stdin.flush()
                stdin.close()
            except Exception:
                pass

        raw_out = stdout.read().decode('utf-8', errors='ignore')
        raw_err = stderr.read().decode('utf-8', errors='ignore')
        exit_code = stdout.channel.recv_exit_status()

        combined_output = (raw_out + ("\n" + raw_err if raw_err else "")).strip()
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        is_active = ("RUNNING" in combined_output) if is_supervisor else ("active (running)" in combined_output or exit_code == 0)

        return {
            'success': exit_code == 0,
            'service_name': display_service,
            'is_active': is_active,
            'output': combined_output,
            'elapsed_ms': elapsed_ms
        }
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


# ==============================================================================
# SQL QUERY RUNNER
# ==============================================================================

def execute_remote_sql_query(server_id=None, app_id=None, query_text="", limit=100):
    """Mengeksekusi query SQL pada database server/aplikasi remote via SSH tunnel."""
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    if not query_text or not query_text.strip():
        raise ValueError("Query SQL tidak boleh kosong.")

    db_host = db_config.get('host') or '127.0.0.1'
    db_port = db_config.get('port') or 5432
    db_name = db_config.get('name')
    db_user = db_config.get('user') or 'postgres'
    db_pass = db_config.get('password') or ''

    if not db_name:
        raise ValueError("Nama database belum dikonfigurasi pada server/aplikasi ini.")

    py_script = f"""
import sys, json, time
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print(json.dumps({{'success': False, 'error': 'Modul psycopg2 tidak terpasang di Python server remote.'}}))
    sys.exit(0)

t0 = time.perf_counter()
query = {repr(query_text.strip())}
try:
    conn = psycopg2.connect(
        host={repr(db_host)},
        port={int(db_port)},
        dbname={repr(db_name)},
        user={repr(db_user)},
        password={repr(db_pass)},
        connect_timeout=10
    )
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query)
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    
    if cur.description:
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany({int(limit)})
        clean_rows = []
        for r in rows:
            clean_rows.append({{k: str(v) if v is not None else None for k, v in r.items()}})
        result = {{
            'success': True,
            'is_select': True,
            'columns': cols,
            'rows': clean_rows,
            'row_count': len(clean_rows),
            'elapsed_ms': elapsed,
            'dbname': {repr(db_name)}
        }}
    else:
        result = {{
            'success': True,
            'is_select': False,
            'row_count': cur.rowcount,
            'elapsed_ms': elapsed,
            'message': f'Query berhasil dieksekusi (Row affected: {{cur.rowcount}}).',
            'dbname': {repr(db_name)}
        }}
    cur.close()
    conn.close()
    print(json.dumps(result))
except Exception as e:
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    print(json.dumps({{'success': False, 'error': str(e), 'elapsed_ms': elapsed}}))
"""

    b64_script = base64.b64encode(py_script.encode('utf-8')).decode('ascii')
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64_script}').decode('utf-8'))\""

    jumps = get_jump_path(target_server.id)
    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        res = subprocess.run(['python3', '-c', py_script], capture_output=True, text=True, timeout=30)
        if res.stdout:
            try:
                return json.loads(res.stdout.strip())
            except Exception:
                pass
        return {'success': False, 'error': res.stderr or 'Gagal mengeksekusi query di lokal.'}

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        stdin, stdout, stderr = target_client.exec_command(cmd, timeout=30)
        raw_out = stdout.read().decode('utf-8', errors='ignore').strip()
        raw_err = stderr.read().decode('utf-8', errors='ignore').strip()

        if raw_out:
            try:
                return json.loads(raw_out)
            except Exception:
                pass
        return {'success': False, 'error': raw_err or raw_out or 'Tidak ada respon dari server database.'}
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


# ==============================================================================
# REMOTE FILE MANAGER & DIRECTORY LIST
# ==============================================================================

def list_remote_directory(server_id=None, app_id=None, target_path=None):
    """Mendapatkan daftar berkas dan folder di direktori remote."""
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    if not target_path or not str(target_path).strip():
        target_path = app_config.get('download_path') or target_server.default_download_path or '/home/webr/apps'

    clean_path = os.path.normpath(str(target_path).strip()).replace('\\', '/')
    if not clean_path.startswith('/'):
        clean_path = '/' + clean_path

    remote_script = f"""
import os, stat, time, json
target = {repr(clean_path)}
if not os.path.exists(target):
    print(json.dumps({{'success': False, 'message': f'Path tidak ditemukan: {{target}}'}}))
    exit(0)

if not os.path.isdir(target):
    target = os.path.dirname(target) or '/'

items = []
try:
    with os.scandir(target) as it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
                is_dir = stat.S_ISDIR(st.st_mode)
                sz = st.st_size if not is_dir else 0
                ext = '' if is_dir else os.path.splitext(entry.name)[1].lower().lstrip('.')
                items.append({{
                    'name': entry.name,
                    'path': os.path.join(target, entry.name).replace('\\\\', '/'),
                    'is_dir': is_dir,
                    'size': sz,
                    'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)),
                    'ext': ext
                }})
            except Exception:
                pass
except Exception as e:
    print(json.dumps({{'success': False, 'message': str(e)}}))
    exit(0)

items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
parent_path = os.path.dirname(target.rstrip('/')) if target != '/' else None
if parent_path == '':
    parent_path = '/'

result = {{
    'success': True,
    'current_path': target,
    'parent_path': parent_path,
    'items': items
}}
print(json.dumps(result))
"""
    b64 = base64.b64encode(remote_script.encode('utf-8')).decode('ascii')
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode('utf-8'))\""

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        stdin, stdout, stderr = target_client.exec_command(cmd, timeout=20)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        if out:
            try:
                return json.loads(out)
            except Exception:
                pass
        return {'success': False, 'message': stderr.read().decode('utf-8', errors='ignore') or 'Gagal membaca direktori.'}
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


class SSHLocalPortForwarder:
    """
    Membuat server proxy TCP lokal (127.0.0.1:port) yang mem-forward koneksi
    melalui SSH channel direct-tcpip menuju remote host:port.
    """
    def __init__(self, ssh_client, remote_host, remote_port):
        self.ssh_client = ssh_client
        self.remote_host = remote_host
        self.remote_port = int(remote_port)
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind(('127.0.0.1', 0))
        self.local_port = self.server_socket.getsockname()[1]
        self.server_socket.listen(5)
        self.running = True
        self.threads = []
        self.server_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.server_thread.start()

    def _listen_loop(self):
        while self.running:
            try:
                self.server_socket.settimeout(0.5)
                local_conn, _ = self.server_socket.accept()
                t = threading.Thread(target=self._forward_conn, args=(local_conn,), daemon=True)
                t.start()
                self.threads.append(t)
            except socket.timeout:
                continue
            except Exception:
                break

    def _forward_conn(self, local_conn):
        try:
            transport = self.ssh_client.get_transport()
            dest_addr = (self.remote_host, self.remote_port)
            src_addr = ('127.0.0.1', 0)
            remote_chan = transport.open_channel("direct-tcpip", dest_addr, src_addr)
            while self.running:
                r, w, x = select.select([local_conn, remote_chan], [], [], 0.5)
                if local_conn in r:
                    data = local_conn.recv(1024 * 32)
                    if len(data) == 0:
                        break
                    remote_chan.send(data)
                if remote_chan in r:
                    data = remote_chan.recv(1024 * 32)
                    if len(data) == 0:
                        break
                    local_conn.send(data)
            remote_chan.close()
            local_conn.close()
        except Exception:
            try:
                local_conn.close()
            except Exception:
                pass

    def stop(self):
        self.running = False
        try:
            self.server_socket.close()
        except Exception:
            pass


def _format_size_bytes(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _find_local_pg_dump():
    p = shutil.which('pg_dump')
    if p:
        return p
    for pattern in [
        'C:/Program Files/PostgreSQL/*/bin/pg_dump.exe',
        'C:/Program Files (x86)/PostgreSQL/*/bin/pg_dump.exe',
        '/usr/lib/postgresql/*/bin/pg_dump',
        '/usr/bin/pg_dump',
        '/usr/local/bin/pg_dump'
    ]:
        matches = glob.glob(pattern)
        if matches:
            return matches[-1]
    return None


def _exec_remote_cmd(client, base_cmd, sudo_pwd=None, use_sudo=False, su_user=None):
    sudo_pwd = sudo_pwd or ""

    def _run_with_sudo(cmd_to_run, as_user=None):
        if "\n" in cmd_to_run or "'" in cmd_to_run or '"' in cmd_to_run or ";" in cmd_to_run or "|" in cmd_to_run or "cd " in cmd_to_run:
            b64_cmd = base64.b64encode(cmd_to_run.encode('utf-8')).decode('ascii')
            base_shell = f"bash -c 'echo \"{b64_cmd}\" | base64 -d | bash'"
        else:
            base_shell = cmd_to_run

        if as_user and as_user.strip() and as_user.strip() != 'root':
            cmd = f"sudo -S -p '' -H -u {as_user.strip()} {base_shell}"
        else:
            cmd = f"sudo -S -p '' -H {base_shell}"

        stdin, stdout, stderr = client.exec_command(cmd)
        if sudo_pwd:
            try:
                stdin.write(f"{sudo_pwd}\n")
                stdin.flush()
                stdin.close()
            except Exception:
                pass
        else:
            try:
                stdin.close()
            except Exception:
                pass
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='ignore')
        err = stderr.read().decode('utf-8', errors='ignore')
        return exit_code, out, err

    if use_sudo:
        code_root, out_root, err_root = _run_with_sudo(base_cmd, None)
        if code_root == 0:
            return out_root
        if su_user and su_user.strip() and su_user.strip() != 'root':
            code_su, out_su, err_su = _run_with_sudo(base_cmd, su_user)
            if code_su == 0:
                return out_su
        stdin, stdout, stderr = client.exec_command(base_cmd)
        if stdout.channel.recv_exit_status() == 0:
            return stdout.read().decode('utf-8', errors='ignore')
        raise PermissionError(err_root or f"Gagal menjalankan sudo (kode {code_root})")

    stdin, stdout, stderr = client.exec_command(base_cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if exit_code == 0:
        return out
    raise Exception(err or out or f"Perintah gagal dengan exit code {exit_code}")


def dump_database(server_id=None, app_id=None, output_dir=None, return_meta=False):
    """
    Eksekusi dump database PostgreSQL:
    1. Coba remote pg_dump via SSH stdout stream ke local .sql.gz
    2. Fallback via SSH tunnel port forwarder + local pg_dump
    3. Fallback via SSH tunnel + db_dumper psycopg2 COPY stream
    """
    t_start = time.perf_counter()
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    output_dir = output_dir or get_monitor_tmp_dir()
    os.makedirs(output_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{app_config['kode']}_{timestamp}.sql.gz"
    local_path = os.path.join(output_dir, filename)

    method_used = ""
    success = False
    jumps = get_jump_path(target_server.id)

    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        local_pg_dump = _find_local_pg_dump()
        if local_pg_dump:
            try:
                env = os.environ.copy()
                env['PGCONNECT_TIMEOUT'] = '15'
                if db_config.get('password'):
                    env['PGPASSWORD'] = str(db_config['password'])
                cmd = [
                    local_pg_dump,
                    '-h', str(db_config['host']),
                    '-p', str(db_config['port']),
                    '-U', str(db_config['user']),
                    '-d', str(db_config['name']),
                    '-w',
                    '--no-owner',
                    '--no-privileges'
                ]
                with gzip.open(local_path, 'wb', compresslevel=1) as gz_out:
                    proc = subprocess.Popen(cmd, stdout=gz_out, stderr=subprocess.PIPE, env=env)
                    _, err = proc.communicate(timeout=600)
                    if proc.returncode == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                        method_used = "Local pg_dump (Direct Native)"
                        success = True
            except Exception:
                pass

        if not success:
            db_dumper.dump_postgres_to_sql(
                host=db_config['host'],
                port=db_config['port'],
                user=db_config['user'],
                password=db_config['password'],
                dbname=db_config['name'],
                output_file=local_path,
                schema=None
            )
            method_used = "psycopg2 COPY Stream (Direct)"
            success = True

        elapsed = round(time.perf_counter() - t_start, 2)
        if return_meta:
            file_sz = os.path.getsize(local_path) if os.path.exists(local_path) else 0
            return {
                'success': True,
                'local_file': local_path,
                'filename': filename,
                'file_size': _format_size_bytes(file_sz),
                'file_size_bytes': file_sz,
                'method': method_used,
                'elapsed_seconds': elapsed,
                'dbname': db_config['name'],
                'host': target_server.host
            }
        return local_path

    target_client, clients, _ = _build_jump_chain(target_server.id)
    forwarder = None
    try:
        try:
            find_pg_cmd = "which pg_dump 2>/dev/null || find /usr/lib/postgresql /usr/pgsql* /usr/local/pgsql /usr/bin -name pg_dump 2>/dev/null | sort -V | tail -n 1"
            stdin, stdout, stderr = target_client.exec_command(find_pg_cmd, timeout=10)
            if stdout.channel.recv_exit_status() == 0:
                remote_pg_dump = stdout.read().decode('utf-8', errors='ignore').strip()
                if remote_pg_dump:
                    pwd_escaped = (db_config.get('password') or '').replace("'", "'\\''")
                    remote_cmd = (
                        f"bash -c \"set -o pipefail; PGCONNECT_TIMEOUT=15 PGPASSWORD='{pwd_escaped}' '{remote_pg_dump}' "
                        f"-h '{db_config['host']}' -p '{db_config['port']}' -U '{db_config['user']}' -d '{db_config['name']}' "
                        f"-w --no-owner --no-privileges | gzip -1 -c\""
                    )
                    stdin, stdout, stderr = target_client.exec_command(remote_cmd, timeout=600)
                    with open(local_path, 'wb', buffering=128 * 1024) as f_out:
                        shutil.copyfileobj(stdout, f_out, length=128 * 1024)
                    rc = stdout.channel.recv_exit_status()
                    if rc == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 100:
                        method_used = "Remote pg_dump (Direct SSH Stream)"
                        success = True
        except Exception:
            pass

        if not success:
            local_pg_dump = _find_local_pg_dump()
            if local_pg_dump:
                try:
                    if not forwarder:
                        forwarder = SSHLocalPortForwarder(
                            ssh_client=target_client,
                            remote_host=db_config['host'],
                            remote_port=db_config['port']
                        )
                    env = os.environ.copy()
                    env['PGCONNECT_TIMEOUT'] = '15'
                    if db_config.get('password'):
                        env['PGPASSWORD'] = str(db_config['password'])
                    cmd = [
                        local_pg_dump,
                        '-h', '127.0.0.1',
                        '-p', str(forwarder.local_port),
                        '-U', str(db_config['user']),
                        '-d', str(db_config['name']),
                        '-w',
                        '--no-owner',
                        '--no-privileges'
                    ]
                    with gzip.open(local_path, 'wb', compresslevel=1) as gz_out:
                        proc = subprocess.Popen(cmd, stdout=gz_out, stderr=subprocess.PIPE, env=env)
                        _, err = proc.communicate(timeout=600)
                        if proc.returncode == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                            method_used = "Local pg_dump (via SSH Tunnel)"
                            success = True
                except Exception:
                    pass

        if not success:
            if not forwarder:
                forwarder = SSHLocalPortForwarder(
                    ssh_client=target_client,
                    remote_host=db_config['host'],
                    remote_port=db_config['port']
                )
            db_dumper.dump_postgres_to_sql(
                host='127.0.0.1',
                port=forwarder.local_port,
                user=db_config['user'],
                password=db_config['password'],
                dbname=db_config['name'],
                output_file=local_path,
                schema=None
            )
            method_used = "psycopg2 COPY Stream (Bulk via Tunnel)"
            success = True

        elapsed = round(time.perf_counter() - t_start, 2)
        if return_meta:
            file_sz = os.path.getsize(local_path) if os.path.exists(local_path) else 0
            return {
                'success': True,
                'local_file': local_path,
                'filename': filename,
                'file_size': _format_size_bytes(file_sz),
                'file_size_bytes': file_sz,
                'method': method_used,
                'elapsed_seconds': elapsed,
                'dbname': db_config['name'],
                'host': target_server.host
            }
        return local_path
    finally:
        if forwarder:
            forwarder.stop()
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def read_remote_log(server_id=None, log_path=None, lines=1000, app_id=None):
    """
    Mengambil potongan baris log terakhir via SSH 'tail -n' dengan hak akses sudo.
    """
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    clean_log_path = (log_path or app_config.get('log_path') or '').strip()
    if not clean_log_path:
        raise ValueError("Path file log belum ditentukan.")

    try:
        lines = max(1, min(int(lines), 100000))
    except (ValueError, TypeError):
        lines = 1000

    jumps = get_jump_path(target_server.id)
    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps and os.path.exists(clean_log_path):
        with open(clean_log_path, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])

    target_client, clients, _ = _build_jump_chain(target_server.id)
    try:
        cmd = f"tail -n {lines} '{clean_log_path}'"
        return _exec_remote_cmd(
            client=target_client,
            base_cmd=cmd,
            sudo_pwd=target_server.ssh_password,
            use_sudo=True,
            su_user=app_config.get('su_user')
        )
    except Exception as e:
        found_logs = []
        try:
            parent_dir = os.path.dirname(clean_log_path) or "/home"
            grandparent = os.path.dirname(parent_dir) if parent_dir not in ['/', '.'] else '/home'
            search_cmd = f"find '{grandparent}' -maxdepth 3 -type f -name '*.log' 2>/dev/null"
            search_out = _exec_remote_cmd(
                client=target_client,
                base_cmd=search_cmd,
                sudo_pwd=target_server.ssh_password,
                use_sudo=True
            )
            for l_item in search_out.splitlines():
                c_item = l_item.strip()
                if c_item and c_item.endswith('.log') and c_item not in found_logs:
                    found_logs.append(c_item)
        except Exception:
            pass

        suggest_msg = ""
        if found_logs:
            items_str = "\n".join([f"  - {f}" for f in found_logs[:10]])
            suggest_msg = f"\n\n[INFO] File log yang ditemukan di server {target_server.host}:\n{items_str}\n\n(Silakan ganti target path log ke salah satu file di atas)."

        raise Exception(f"{str(e)}{suggest_msg}")
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def download_remote_log_file(server_id=None, log_path=None, output_dir=None, app_id=None):
    """
    Mengunduh berkas log lengkap dari server target via SSH stream.
    """
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    clean_log_path = (log_path or app_config.get('log_path') or '').strip()
    if not clean_log_path:
        raise ValueError("Path file log belum ditentukan.")

    filename = f"{app_config['kode']}_{os.path.basename(clean_log_path)}"
    output_dir = output_dir or get_monitor_tmp_dir()
    os.makedirs(output_dir, exist_ok=True)
    local_path = os.path.join(output_dir, filename)

    jumps = get_jump_path(target_server.id)
    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps and os.path.exists(clean_log_path):
        shutil.copyfile(clean_log_path, local_path)
        return local_path

    target_client, clients, _ = _build_jump_chain(target_server.id)
    try:
        sudo_pwd = target_server.ssh_password or ""
        cat_cmd = f"sudo -S -p '' -H cat '{clean_log_path}'"
        stdin, stdout, stderr = target_client.exec_command(cat_cmd)
        if sudo_pwd:
            try:
                stdin.write(f"{sudo_pwd}\n")
                stdin.flush()
                stdin.close()
            except Exception:
                pass
        with open(local_path, 'wb') as f_out:
            shutil.copyfileobj(stdout, f_out, length=64 * 1024)
        return local_path
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def execute_remote_git_command(server_id=None, app_id=None, target_path=None, git_cmd="git pull", git_username=None, git_password=None, chmod_file="640", chmod_dir="775", enable_chmod=False):
    """
    Mengeksekusi perintah Git pada path aplikasi remote via SSH.
    """
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    resolved_path = (target_path or "").strip()
    if not resolved_path:
        if app_config.get('download_path') and app_config['download_path'].strip():
            resolved_path = app_config['download_path'].strip()
        elif app_config.get('ini_path') and app_config['ini_path'].strip():
            resolved_path = os.path.dirname(app_config['ini_path'].strip())
        else:
            resolved_path = "/home/webr/apps"

    clean_cmd = (git_cmd or "git pull").strip()
    if clean_cmd.startswith("git "):
        git_subcmd = clean_cmd[4:].strip()
        display_cmd = f"git {git_subcmd}"
    elif clean_cmd.startswith("git"):
        git_subcmd = clean_cmd[3:].strip() or "pull"
        display_cmd = f"git {git_subcmd}"
    else:
        git_subcmd = clean_cmd
        display_cmd = clean_cmd

    u_clean = (git_username or "").strip()
    p_clean = (git_password or "").strip()

    if enable_chmod:
        c_file = (chmod_file or "").strip()
        c_dir = (chmod_dir or "").strip()
    else:
        c_file = ""
        c_dir = ""

    if (u_clean or p_clean) and git_subcmd.startswith("pull"):
        display_cmd = f"git {git_subcmd} (auth: {u_clean or 'token'})"

    chmod_script_part = ""
    if c_dir:
        chmod_script_part += f"\nfind . -type d -exec chmod {c_dir} {{}} +"
    if c_file:
        chmod_script_part += f"\nfind . -type f -exec chmod {c_file} {{}} +"

    script_content = f"""cd "{resolved_path}" 2>/dev/null || {{ echo "ERROR: Path tidak ditemukan: {resolved_path}"; exit 1; }}
export GIT_TERMINAL_PROMPT=0
echo "=== TARGET: {resolved_path} ==="
echo "=== EXECUTING: {display_cmd} ==="
git {git_subcmd}
RC=$?
if [ $RC -eq 0 ]; then
    echo "=== GIT STATUS ==="
    git status -s
    {chmod_script_part}
fi
exit $RC
"""
    t_start = time.perf_counter()
    jumps = get_jump_path(target_server.id)

    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        res = subprocess.run(script_content, shell=True, capture_output=True, text=True, timeout=60)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        out = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        return {
            'success': res.returncode == 0,
            'cmd': display_cmd,
            'path': resolved_path,
            'output': out,
            'elapsed_ms': elapsed_ms,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        raw_output = _exec_remote_cmd(
            client=target_client,
            base_cmd=script_content,
            sudo_pwd=target_server.ssh_password,
            use_sudo=app_config.get('use_sudo', False),
            su_user=app_config.get('su_user')
        )
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            'success': True,
            'cmd': display_cmd,
            'path': resolved_path,
            'output': raw_output.strip(),
            'elapsed_ms': elapsed_ms,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            'success': False,
            'cmd': display_cmd,
            'path': resolved_path,
            'output': str(e),
            'elapsed_ms': elapsed_ms,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def execute_remote_terminal_command(server_id=None, app_id=None, command=None, workdir=None, use_sudo=False, su_user=None, sudo_password=None):
    """
    Eksekusi perintah terminal kustom pada server remote via SSH.
    """
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    cmd_raw = (command or "").strip() or "uptime"
    current_dir = (workdir or "").strip() or app_config.get('download_path') or (target_server.default_download_path or "/home/webr")

    effective_sudo = use_sudo if use_sudo is not None else app_config.get('use_sudo', False)
    effective_su = (su_user if su_user is not None else app_config.get('su_user')) or ""
    sudo_pwd = sudo_password if sudo_password is not None else (target_server.ssh_password or "")

    t_start = time.perf_counter()
    script_wrapped = f"""cd "{current_dir}" 2>/dev/null
{cmd_raw}
MANDB_EXIT_CODE=$?
echo '__MANDB_PWD__'
pwd
exit $MANDB_EXIT_CODE
"""
    jumps = get_jump_path(target_server.id)

    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        res = subprocess.run(script_wrapped, shell=True, capture_output=True, text=True, timeout=60)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        raw_out = (res.stdout or "")
        raw_err = (res.stderr or "")
        new_pwd = current_dir
        if '__MANDB_PWD__' in raw_out:
            parts = raw_out.split('__MANDB_PWD__')
            cleaned_out = parts[0].rstrip('\r\n')
            pwd_lines = [l.strip() for l in parts[1].splitlines() if l.strip()]
            if pwd_lines:
                new_pwd = pwd_lines[0]
        else:
            cleaned_out = raw_out.strip()

        combined = (cleaned_out + ("\n" + raw_err.strip() if raw_err.strip() else "")).strip()
        return {
            'cmd': cmd_raw,
            'output': combined or "(Perintah berhasil tanpa output)",
            'exit_code': res.returncode,
            'elapsed_ms': elapsed_ms,
            'workdir': new_pwd,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }

    clients = []
    try:
        target_client, clients, _ = _build_jump_chain(target_server.id)
        raw_output = _exec_remote_cmd(
            client=target_client,
            base_cmd=script_wrapped,
            sudo_pwd=sudo_pwd,
            use_sudo=effective_sudo,
            su_user=effective_su
        )
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        new_pwd = current_dir
        if '__MANDB_PWD__' in raw_output:
            parts = raw_output.split('__MANDB_PWD__')
            cleaned_out = parts[0].rstrip('\r\n')
            pwd_lines = [l.strip() for l in parts[1].splitlines() if l.strip()]
            if pwd_lines:
                new_pwd = pwd_lines[0]
        else:
            cleaned_out = raw_output.strip()

        return {
            'cmd': cmd_raw,
            'output': cleaned_out or "(Perintah berhasil tanpa output)",
            'exit_code': 0,
            'elapsed_ms': elapsed_ms,
            'workdir': new_pwd,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            'cmd': cmd_raw,
            'output': str(e),
            'exit_code': 1,
            'elapsed_ms': elapsed_ms,
            'workdir': current_dir,
            'server_nama': target_server.nama,
            'server_host': target_server.host
        }
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def get_server_user_access(server_id=None, app_id=None, filter_pegawai='all'):
    """
    Mengambil data akses user database aplikasi target.
    """
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)
    jumps = get_jump_path(target_server.id)

    if target_server.host in ['127.0.0.1', 'localhost'] and not jumps:
        conn = psycopg2.connect(
            host='127.0.0.1',
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            dbname=db_config['name'],
            connect_timeout=15
        )
        conn.autocommit = True
        user_records = user_access_helper.fetch_user_access_data(conn, filter_pegawai=filter_pegawai)
        conn.close()
        return user_records, target_server, app_obj

    target_client, clients, _ = _build_jump_chain(target_server.id)
    forwarder = None
    try:
        forwarder = SSHLocalPortForwarder(
            ssh_client=target_client,
            remote_host=db_config['host'],
            remote_port=db_config['port']
        )
        conn = psycopg2.connect(
            host='127.0.0.1',
            port=forwarder.local_port,
            user=db_config['user'],
            password=db_config['password'],
            dbname=db_config['name'],
            connect_timeout=15
        )
        conn.autocommit = True
        user_records = user_access_helper.fetch_user_access_data(conn, filter_pegawai=filter_pegawai)
        conn.close()
        return user_records, target_server, app_obj
    finally:
        if forwarder:
            forwarder.stop()
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass


def download_remote_path(server_id=None, remote_path=None, use_sudo=False, sudo_password=None, su_user=None, output_dir=None, app_id=None):
    """
    Mengunduh file atau folder kustom dari server target secara langsung melalui stream SSH stdout.
    """
    output_dir = output_dir or get_monitor_tmp_dir()
    os.makedirs(output_dir, exist_ok=True)
    target_server, app_obj, db_config, app_config = _resolve_target_context(server_id, app_id)

    resolved_path = remote_path or app_config.get('download_path')
    if not resolved_path or not resolved_path.strip():
        raise ValueError("Remote path tidak boleh kosong.")

    resolved_path = resolved_path.strip().rstrip('/')
    target_client, clients, _ = _build_jump_chain(target_server.id)
    try:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '_', os.path.basename(resolved_path)) or 'files'
        tar_filename = f"{app_config['kode']}_{sanitized_name}_{timestamp}.tar.gz"
        local_path = os.path.join(output_dir, tar_filename)

        parent_dir = os.path.dirname(resolved_path) or "/"
        target_name = os.path.basename(resolved_path)

        effective_sudo = use_sudo if use_sudo is not None else app_config.get('use_sudo')
        effective_su = su_user if su_user is not None else app_config.get('su_user')
        sudo_pwd = sudo_password if sudo_password is not None else (target_server.ssh_password or "")

        if effective_sudo:
            if effective_su and effective_su.strip():
                su_clean = effective_su.strip()
                cmd = f"sudo -S -p '' -H -u {su_clean} tar -czf - -C '{parent_dir}' '{target_name}'"
            else:
                cmd = f"sudo -S -p '' -H tar -czf - -C '{parent_dir}' '{target_name}'"
        else:
            cmd = f"tar -czf - -C '{parent_dir}' '{target_name}'"

        stdin, stdout, stderr = target_client.exec_command(cmd)
        if effective_sudo and sudo_pwd:
            try:
                stdin.write(f"{sudo_pwd}\n")
                stdin.flush()
                stdin.close()
            except Exception:
                pass

        with open(local_path, 'wb') as f_out:
            shutil.copyfileobj(stdout, f_out, length=64 * 1024)

        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err_msg = stderr.read().decode('utf-8', errors='ignore')
            raise Exception(f"Gagal mengompresi remote path (status {exit_status}): {err_msg}")

        return local_path
    finally:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass

