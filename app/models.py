from datetime import datetime
from decimal import Decimal
from sqlalchemy import (String, Integer, DateTime, Date, Numeric, Text, ForeignKey,
    ForeignKeyConstraint, UniqueConstraint, Boolean, Index)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(String(255))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(default=0)
class Workspace(Base):
    __tablename__ = 'workspaces'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
class Membership(Base):
    __tablename__ = 'workspace_memberships'
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default='owner')
class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'))
    name: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint('workspace_id','name'), UniqueConstraint('workspace_id','id'))
class Store(Base):
    __tablename__ = 'stores'
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'))
    name: Mapped[str] = mapped_column(String(150))
    normalized: Mapped[str] = mapped_column(String(150))
    __table_args__ = (UniqueConstraint('workspace_id','normalized'), UniqueConstraint('workspace_id','id'))
class Receipt(Base):
    __tablename__ = 'receipts'
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    attempts: Mapped[int] = mapped_column(default=0)
    job_token: Mapped[str | None] = mapped_column(String(36))
    quota_month: Mapped[str] = mapped_column(String(7))
    quota_reserved: Mapped[bool] = mapped_column(Boolean, default=True)
    store_id: Mapped[int | None] = mapped_column(Integer)
    store_guess: Mapped[str] = mapped_column(String(150), default='')
    purchase_date: Mapped[datetime | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default='USD')
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    discount: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    draft: Mapped[str] = mapped_column(Text, default='{}')
    flags: Mapped[str] = mapped_column(Text, default='[]')
    __table_args__ = (UniqueConstraint('workspace_id','sha256'), UniqueConstraint('workspace_id','id'),
        ForeignKeyConstraint(['workspace_id','store_id'],['stores.workspace_id','stores.id']))
class Item(Base):
    __tablename__ = 'items'
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'))
    receipt_id: Mapped[int] = mapped_column(Integer)
    category_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12,3))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14,2))
    __table_args__ = (
        Index('ix_items_workspace_receipt','workspace_id','receipt_id'),
        Index('ix_items_workspace_category','workspace_id','category_id'),
        ForeignKeyConstraint(['workspace_id','receipt_id'], ['receipts.workspace_id','receipts.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['workspace_id','category_id'], ['categories.workspace_id','categories.id']))
class Subscription(Base):
    __tablename__ = 'subscriptions'
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    subscription_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(30), default='free')
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
class Usage(Base):
    __tablename__ = 'usage_records'
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    used: Mapped[int] = mapped_column(default=0)
class MailToken(Base):
    __tablename__ = 'mail_tokens'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    purpose: Mapped[str] = mapped_column(String(16))
    expires: Mapped[datetime] = mapped_column(DateTime)
class StripeEvent(Base):
    __tablename__ = 'stripe_events'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
class Deletion(Base):
    __tablename__ = 'file_deletions'
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id'))
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt: Mapped[datetime] = mapped_column(default=datetime.utcnow)
