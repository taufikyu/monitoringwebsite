from __future__ import annotations
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user

def permission_required(perm_code: str):
    """
    Decorator for Flask route endpoints to enforce permission access.
    Redirects to dashboard with a flash message if the user lacks the required permission.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login', next=request.url))
            if not current_user.has_permission(perm_code):
                flash(f'Akses ditolak: Anda tidak memiliki izin ({perm_code}) untuk membuka halaman ini.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
