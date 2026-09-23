# ============================================================================
# Nonprofit documents — what a fund-accounting ledger posts that a business
# never does.
#
#   RestrictionRelease   — a restricted fund spent money for its purpose;
#                          move that much from "with donor restrictions" to
#                          "without" (DR 3400 / CR 3300, tagged to the fund).
#   AllocationRule       — a saved way to split a shared cost (rent, the
#                          office manager's payroll) across funds and/or
#                          functions: by percent, square feet, or hours.
#   FunctionalAllocation — a period-end run of a rule: one balanced entry
#                          that reclasses the unassigned cost on the source
#                          account into program / management / fundraising
#                          on the same natural account, so the Statement of
#                          Functional Expenses has rows and the P&L is
#                          untouched. Voidable like a job cost entry.
#
# Money lives on ledger lines as everywhere else; these tables are the
# documents that explain the postings.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

ALLOCATION_BASES = ("percent", "square_feet", "hours")
DOC_STATUSES = ("posted", "void")


class RestrictionRelease(Base):
    __tablename__ = "restriction_releases"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(30), nullable=False, unique=True)
    date = Column(Date, nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False, index=True)
    amount = Column(Numeric(15, 2), nullable=False)
    # The period whose spending this release covers (informational)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    memo = Column(Text, nullable=True)
    status = Column(String(10), nullable=False, default="posted")
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fund = relationship("TxnClass")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class AllocationRule(Base):
    __tablename__ = "allocation_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    basis = Column(String(20), nullable=False, default="percent")
    # What gets split: the unassigned (function IS NULL) expense on this
    # account and/or in this fund for the period. Both NULL = every
    # unassigned expense line.
    source_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    source_class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source_account = relationship("Account")
    source_class = relationship("TxnClass")
    targets = relationship(
        "AllocationRuleTarget",
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="AllocationRuleTarget.line_order",
    )


class AllocationRuleTarget(Base):
    __tablename__ = "allocation_rule_targets"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(
        Integer,
        ForeignKey("allocation_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Where the share lands: a fund, a function, or both
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    function = Column(String(20), nullable=True)
    # Hours basis: the job (grant) whose time entries weight this target
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    weight = Column(Numeric(12, 4), nullable=False, default=1)
    line_order = Column(Integer, nullable=False, default=0)

    rule = relationship("AllocationRule", back_populates="targets")
    fund = relationship("TxnClass")
    job = relationship("Job")


class FunctionalAllocation(Base):
    __tablename__ = "functional_allocations"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(30), nullable=False, unique=True)
    date = Column(Date, nullable=False, index=True)
    rule_id = Column(Integer, ForeignKey("allocation_rules.id"), nullable=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    memo = Column(Text, nullable=True)
    status = Column(String(10), nullable=False, default="posted")
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    total = Column(Numeric(15, 2), nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    rule = relationship("AllocationRule")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    lines = relationship(
        "FunctionalAllocationLine",
        back_populates="allocation",
        cascade="all, delete-orphan",
        order_by="FunctionalAllocationLine.line_order",
    )


class FunctionalAllocationLine(Base):
    __tablename__ = "functional_allocation_lines"

    id = Column(Integer, primary_key=True, index=True)
    allocation_id = Column(
        Integer,
        ForeignKey("functional_allocations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    function = Column(String(20), nullable=True)
    weight = Column(Numeric(12, 4), nullable=True)
    amount = Column(Numeric(15, 2), nullable=False)
    description = Column(Text, nullable=True)
    line_order = Column(Integer, nullable=False, default=0)

    allocation = relationship("FunctionalAllocation", back_populates="lines")
    account = relationship("Account")
    fund = relationship("TxnClass")
