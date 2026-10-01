from __future__ import annotations
from datetime import datetime
from core.extensions import db

class Server(db.Model):
    __tablename__ = 'mandb_servers'
    
    id = db.Column(db.Integer, primary_key=True)
    kode = db.Column(db.String(32), unique=True, nullable=True)
    nama = db.Column(db.String(128), nullable=False)
    host = db.Column(db.String(128), nullable=False)
    ssh_port = db.Column(db.Integer, default=22)
    ssh_user = db.Column(db.String(64), nullable=True)
    ssh_password = db.Column(db.String(128), nullable=True)
    ssh_key_path = db.Column(db.String(256), nullable=True)
    
    db_host = db.Column(db.String(128), default='127.0.0.1')
    db_port = db.Column(db.Integer, default=5432)
    db_name = db.Column(db.String(128), nullable=True)
    db_user = db.Column(db.String(64), nullable=True)
    db_password = db.Column(db.String(128), nullable=True)
    dump_method = db.Column(db.String(32), default='script')

    app_ini_path = db.Column(db.String(256), nullable=True)
    default_download_path = db.Column(db.String(256), nullable=True)
    use_sudo = db.Column(db.Boolean, default=False)
    su_user = db.Column(db.String(64), nullable=True)
    status = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    apps = db.relationship('ServerApp', backref='server', cascade='all, delete-orphan', lazy=True)
    jumps = db.relationship('ServerJump', foreign_keys='ServerJump.server_id', backref='server', order_by='ServerJump.urutan', cascade='all, delete-orphan', lazy=True)

    def __repr__(self):
        return f"<Server {self.kode or self.id}: {self.nama} ({self.host})>"


class ServerJump(db.Model):
    __tablename__ = 'mandb_server_jumps'
    
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey('mandb_servers.id', ondelete='CASCADE'), nullable=False)
    jump_server_id = db.Column(db.Integer, db.ForeignKey('mandb_servers.id', ondelete='CASCADE'), nullable=False)
    urutan = db.Column(db.Integer, default=1)

    jump_server = db.relationship('Server', foreign_keys=[jump_server_id])

    __table_args__ = (
        db.UniqueConstraint('server_id', 'jump_server_id', name='server_jump_uq'),
    )

    def __repr__(self):
        return f"<ServerJump server={self.server_id} -> jump={self.jump_server_id} seq={self.urutan}>"


class ServerApp(db.Model):
    __tablename__ = 'mandb_server_apps'
    
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey('mandb_servers.id', ondelete='CASCADE'), nullable=False)
    kode = db.Column(db.String(32), nullable=True)
    nama = db.Column(db.String(128), nullable=False)
    
    app_ini_path = db.Column(db.String(256), nullable=True)
    app_log_path = db.Column(db.String(256), nullable=True)
    service_type = db.Column(db.String(32), default='systemd')
    service_path = db.Column(db.String(256), nullable=True)
    default_download_path = db.Column(db.String(256), nullable=True)
    use_sudo = db.Column(db.Boolean, default=False)
    su_user = db.Column(db.String(64), nullable=True)
    
    db_host = db.Column(db.String(128), default='127.0.0.1')
    db_port = db.Column(db.Integer, default=5432)
    db_name = db.Column(db.String(128), nullable=True)
    db_user = db.Column(db.String(64), nullable=True)
    db_password = db.Column(db.String(128), nullable=True)
    dump_method = db.Column(db.String(32), default='script')
    status = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('server_id', 'kode', name='server_app_kode_uq'),
    )

    def __repr__(self):
        return f"<ServerApp {self.kode or self.id}: {self.nama}>"


class ActivityLog(db.Model):
    __tablename__ = 'mandb_activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    user_name = db.Column(db.String(128), default='system')
    server_id = db.Column(db.Integer, db.ForeignKey('mandb_servers.id', ondelete='SET NULL'), nullable=True)
    app_id = db.Column(db.Integer, db.ForeignKey('mandb_server_apps.id', ondelete='SET NULL'), nullable=True)
    action_type = db.Column(db.String(64), nullable=False)
    target_title = db.Column(db.String(256), nullable=True)
    description = db.Column(db.String(1024), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(32), default='SUCCESS')
    details = db.Column(db.Text, nullable=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)

    server = db.relationship('Server', foreign_keys=[server_id])
    app = db.relationship('ServerApp', foreign_keys=[app_id])

    def __repr__(self):
        return f"<ActivityLog {self.action_type} by {self.user_name} [{self.status}]>"


class QueryHistory(db.Model):
    __tablename__ = 'mandb_query_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    user_name = db.Column(db.String(128), default='system')
    server_id = db.Column(db.Integer, db.ForeignKey('mandb_servers.id', ondelete='SET NULL'), nullable=True)
    app_id = db.Column(db.Integer, db.ForeignKey('mandb_server_apps.id', ondelete='SET NULL'), nullable=True)
    query_text = db.Column(db.Text, nullable=False)
    elapsed_ms = db.Column(db.Integer, default=0)
    row_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(32), default='SUCCESS')
    error_message = db.Column(db.Text, nullable=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)

    server = db.relationship('Server', foreign_keys=[server_id])
    app = db.relationship('ServerApp', foreign_keys=[app_id])

    def __repr__(self):
        return f"<QueryHistory id={self.id} status={self.status}>"
