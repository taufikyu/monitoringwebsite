from __future__ import annotations
from flask_login import UserMixin
from .extensions import db

# =========================================================================
# Core RBAC Association Tables
# =========================================================================

user_roles = db.Table(
    'user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True)
)

role_permissions = db.Table(
    'role_permissions',
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True)
)


# =========================================================================
# Permission Model
# =========================================================================

class Permission(db.Model):
    __tablename__ = 'permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)   # e.g. 'menu.dashboard', 'admin.users'
    name = db.Column(db.String(150), nullable=False)               # e.g. 'Akses Dashboard Utama'
    category = db.Column(db.String(100), default='Umum')           # e.g. 'Navigasi', 'Manajemen Sistem'
    description = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<Permission {self.code}: {self.name}>"


# =========================================================================
# Role / User Group Model
# =========================================================================

class Role(db.Model):
    __tablename__ = 'roles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)   # e.g. 'Administrator', 'Member'
    description = db.Column(db.Text, nullable=True)
    is_system = db.Column(db.Boolean, default=False)               # System roles cannot be deleted
    
    permissions = db.relationship(
        'Permission',
        secondary=role_permissions,
        lazy='subquery',
        backref=db.backref('roles', lazy=True)
    )

    def has_permission(self, perm_code: str) -> bool:
        if self.name == 'Administrator':
            return True
        return any(p.code == perm_code for p in self.permissions)

    def __repr__(self):
        return f"<Role {self.name}>"


# =========================================================================
# Core Platform User Model
# =========================================================================

class User(db.Model, UserMixin):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    level = db.Column(db.Integer, default=1)  # Legacy level (1: Member, 2: Pro, 3: Admin)
    is_admin = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)

    roles = db.relationship(
        'Role',
        secondary=user_roles,
        lazy='subquery',
        backref=db.backref('users', lazy=True)
    )

    @property
    def effective_level(self):
        if self.is_admin:
            return 3
        return self.level or 1

    @property
    def level_badge(self):
        if self.is_admin or self.level == 3:
            return ('Administrator', 'badge-admin')
        elif self.roles:
            first_role = self.roles[0].name
            return (first_role, 'badge-pro')
        elif self.level == 2:
            return ('Pro / Creator', 'badge-pro')
        return ('Member', 'badge-user')

    @property
    def role_names(self) -> list[str]:
        if self.roles:
            return [r.name for r in self.roles]
        if self.is_admin:
            return ['Administrator']
        return ['Member']

    def has_permission(self, perm_code: str) -> bool:
        """Checks if user has a specific permission code through roles or admin flag."""
        if not perm_code:
            return True
        if self.is_admin or self.effective_level == 3:
            return True
        for role in self.roles:
            if role.has_permission(perm_code):
                return True
        return False

    def get_permission_codes(self) -> set[str]:
        """Returns set of all permission codes granted to the user."""
        if self.is_admin or self.effective_level == 3:
            all_perms = Permission.query.all()
            return {p.code for p in all_perms}
        perms = set()
        for role in self.roles:
            for p in role.permissions:
                perms.add(p.code)
        return perms


# =========================================================================
# Dynamic Application Menu Model
# =========================================================================

class AppMenu(db.Model):
    __tablename__ = 'app_menus'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)   # e.g. 'home', 'admin_users'
    title = db.Column(db.String(100), nullable=False)             # e.g. 'Dashboard'
    route = db.Column(db.String(150), nullable=False)             # Flask endpoint e.g. 'home'
    icon = db.Column(db.String(100), default='ph ph-link')        # Phosphor icon class
    group_name = db.Column(db.String(100), default='Navigasi')    # Category group header
    permission_code = db.Column(db.String(100), nullable=True)    # Linked permission code e.g. 'menu.dashboard'
    min_level = db.Column(db.Integer, default=1)                  # Legacy fallback min level (1: Member, 2: Pro, 3: Admin)
    order = db.Column(db.Integer, default=10)                     # Sorting weight
    is_active = db.Column(db.Boolean, default=True)               # Active toggle
    endpoints = db.Column(db.Text, nullable=True)                 # Comma-separated active endpoints
    parent_id = db.Column(db.Integer, db.ForeignKey('app_menus.id'), nullable=True)
    
    children = db.relationship('AppMenu', backref=db.backref('parent', remote_side=[id]), order_by='AppMenu.order')

    def get_endpoint_list(self):
        if self.endpoints:
            return [ep.strip() for ep in self.endpoints.split(',') if ep.strip()]
        return [self.route]
