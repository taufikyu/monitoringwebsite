from __future__ import annotations
import json
import logging
from datetime import datetime
from flask import request
from flask_login import current_user
from core.extensions import db
from modules.monitor.models import ActivityLog, QueryHistory

log = logging.getLogger(__name__)

def extract_user_info():
    """Extract user_id, user_name, and client ip from Flask request context."""
    user_id = None
    user_name = 'guest'
    if current_user and current_user.is_authenticated:
        user_id = getattr(current_user, 'id', None)
        user_name = getattr(current_user, 'name', None) or getattr(current_user, 'email', None) or str(user_id)

    ip_address = request.headers.get('X-Forwarded-For')
    if ip_address:
        ip_address = ip_address.split(',')[0].strip()
    else:
        ip_address = request.remote_addr or '127.0.0.1'

    return user_id, user_name, ip_address


def log_activity(action_type: str, description: str = '', server_id: int | None = None,
                 app_id: int | None = None, target_title: str | None = None,
                 status: str = 'SUCCESS', details: dict | str | None = None):
    """Mencatat log aktivitas pengguna ke database SQLite (mandb_activity_logs)."""
    try:
        user_id, user_name, ip_address = extract_user_info()
        details_str = None
        if details is not None:
            if isinstance(details, (dict, list)):
                details_str = json.dumps(details, ensure_ascii=False)
            else:
                details_str = str(details)

        activity = ActivityLog(
            user_id=user_id,
            user_name=user_name,
            server_id=server_id,
            app_id=app_id,
            action_type=action_type,
            target_title=target_title,
            description=description,
            ip_address=ip_address,
            status=status,
            details=details_str,
            created=datetime.utcnow()
        )
        db.session.add(activity)
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        log.warning(f"Gagal mencatat activity log: {e}")


def record_query_history(query_text: str, elapsed_ms: int = 0, row_count: int = 0,
                         status: str = 'SUCCESS', error_message: str | None = None,
                         server_id: int | None = None, app_id: int | None = None):
    """Mencatat riwayat eksekusi query SQL ke database SQLite (mandb_query_history)."""
    try:
        user_id, user_name, _ = extract_user_info()
        qh = QueryHistory(
            user_id=user_id,
            user_name=user_name,
            server_id=server_id,
            app_id=app_id,
            query_text=query_text.strip(),
            elapsed_ms=elapsed_ms,
            row_count=row_count,
            status=status,
            error_message=error_message,
            created=datetime.utcnow()
        )
        db.session.add(qh)
        db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        log.warning(f"Gagal mencatat query history: {e}")


def get_recent_query_history(server_id: int | None = None, app_id: int | None = None, limit: int = 20):
    """Mengambil riwayat query terakhir untuk server atau aplikasi tertentu."""
    try:
        q = QueryHistory.query
        if app_id:
            q = q.filter(QueryHistory.app_id == app_id)
        elif server_id:
            q = q.filter((QueryHistory.server_id == server_id) & (QueryHistory.app_id.is_(None)))

        records = q.order_by(QueryHistory.id.desc()).limit(limit).all()
        return [{
            'id': r.id,
            'query_text': r.query_text,
            'elapsed_ms': r.elapsed_ms,
            'row_count': r.row_count,
            'status': r.status,
            'created': r.created.strftime('%Y-%m-%d %H:%M:%S') if r.created else '-',
            'user_name': r.user_name
        } for r in records]
    except Exception as e:
        log.warning(f"Gagal membaca riwayat query: {e}")
        return []
