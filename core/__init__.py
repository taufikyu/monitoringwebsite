from __future__ import annotations
from .extensions import db, login_manager, bcrypt, oauth, mail, modular_menus
from .models import User, AppMenu

__all__ = ['db', 'login_manager', 'bcrypt', 'oauth', 'mail', 'modular_menus', 'User', 'AppMenu']
