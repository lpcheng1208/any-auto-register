"""数据库模型 - SQLite via SQLModel"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import inspect
from sqlmodel import Field, SQLModel, Session, create_engine, select


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DATABASE_URL = "sqlite:///data/account_manager.db"
engine = create_engine(DATABASE_URL)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountModel(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    email: str = Field(index=True)
    password: str
    user_id: str = ""
    region: str = ""
    token: str = ""
    status: str = "registered"
    trial_end_time: int = 0
    cashier_url: str = ""
    extra_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, data: dict) -> None:
        self.extra_json = json.dumps(data, ensure_ascii=False)


class TaskRun(SQLModel, table=True):
    __tablename__ = "task_runs"

    id: str = Field(primary_key=True)
    task_type: str = Field(index=True)
    trigger_source: str = Field(default="manual", index=True)
    status: str = Field(default="pending", index=True)
    target_platform: Optional[str] = Field(default=None, index=True)
    payload_json: str = "{}"
    summary_json: str = "{}"
    total_count: int = 0
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    parent_task_id: Optional[str] = Field(default=None, index=True)
    scheduler_key: str = ""
    error: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_payload(self) -> dict:
        return json.loads(self.payload_json or "{}")

    def get_summary(self) -> dict:
        return json.loads(self.summary_json or "{}")


class TaskLog(SQLModel, table=True):
    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(default="", index=True)
    item_type: str = "account"
    item_key: str = ""
    platform: str = ""
    email: str = ""
    status: str = "failed"
    error: str = ""
    detail_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)

    def get_detail(self) -> dict:
        return json.loads(self.detail_json or "{}")


class TaskEvent(SQLModel, table=True):
    __tablename__ = "task_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    level: str = "info"
    message: str
    created_at: datetime = Field(default_factory=_utcnow)


class ScheduledRegisterTask(SQLModel, table=True):
    __tablename__ = "scheduled_register_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    enabled: bool = True
    platform: str = Field(index=True)
    interval_minutes: int = 60
    payload_json: str = "{}"
    next_run_at: Optional[datetime] = Field(default=None, index=True)
    last_run_at: Optional[datetime] = None
    last_task_id: str = ""
    last_status: str = "idle"
    last_error: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json or "{}")


class ProxyModel(SQLModel, table=True):
    __tablename__ = "proxies"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    region: str = ""
    success_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    last_checked: Optional[datetime] = None


def _ensure_task_log_columns() -> None:
    inspector = inspect(engine)
    if "task_logs" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("task_logs")}
    statements = []
    if "task_id" not in columns:
        statements.append("ALTER TABLE task_logs ADD COLUMN task_id TEXT NOT NULL DEFAULT ''")
    if "item_type" not in columns:
        statements.append("ALTER TABLE task_logs ADD COLUMN item_type TEXT NOT NULL DEFAULT 'account'")
    if "item_key" not in columns:
        statements.append("ALTER TABLE task_logs ADD COLUMN item_key TEXT NOT NULL DEFAULT ''")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id)"
        )


def save_account(account) -> AccountModel:
    """从 base_platform.Account 存入数据库（同平台同邮箱则更新）"""
    with Session(engine) as session:
        existing = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == account.platform)
            .where(AccountModel.email == account.email)
        ).first()
        if existing:
            existing.password = account.password
            existing.user_id = account.user_id or ""
            existing.region = account.region or ""
            existing.token = account.token or ""
            existing.status = account.status.value
            existing.trial_end_time = account.trial_end_time or 0
            existing.extra_json = json.dumps(account.extra or {}, ensure_ascii=False)
            existing.cashier_url = (account.extra or {}).get("cashier_url", "")
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        model = AccountModel(
            platform=account.platform,
            email=account.email,
            password=account.password,
            user_id=account.user_id or "",
            region=account.region or "",
            token=account.token or "",
            status=account.status.value,
            trial_end_time=account.trial_end_time or 0,
            extra_json=json.dumps(account.extra or {}, ensure_ascii=False),
            cashier_url=(account.extra or {}).get("cashier_url", ""),
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return model


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _ensure_task_log_columns()


def get_session():
    with Session(engine) as session:
        yield session
