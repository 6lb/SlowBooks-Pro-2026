# ============================================================================
# In-kind gifts — a donated piano, a pallet of paint, pro bono legal work.
#
# Two-sided by nature: DR the asset or expense the gift is (the piano goes
# on the books, the paint is program supplies), CR In-Kind Contributions
# income, both tagged to the fund. An item line on a receipt can only
# credit income, so this is its own small document, shaped like a job
# cost entry: every line names both accounts. The donor's acknowledgment
# describes the property and never states a value — the donor values the
# gift, not the charity (IRS Publication 1771).
# ============================================================================

from sqlalchemy import (
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


class InKindGift(Base):
    __tablename__ = "in_kind_gifts"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(30), nullable=False, unique=True)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    memo = Column(Text, nullable=True)
    status = Column(String(10), nullable=False, default="posted")
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    total = Column(Numeric(15, 2), nullable=False, default=0)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    fund = relationship("TxnClass")
    lines = relationship(
        "InKindGiftLine",
        back_populates="gift",
        cascade="all, delete-orphan",
        order_by="InKindGiftLine.line_order",
    )


class InKindGiftLine(Base):
    __tablename__ = "in_kind_gift_lines"

    id = Column(Integer, primary_key=True, index=True)
    gift_id = Column(
        Integer,
        ForeignKey("in_kind_gifts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description = Column(Text, nullable=False)
    quantity = Column(Numeric(15, 2), nullable=False, default=1)
    fair_value = Column(Numeric(15, 2), nullable=False, default=0)  # per unit
    amount = Column(Numeric(15, 2), nullable=False, default=0)
    debit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    credit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    line_order = Column(Integer, nullable=False, default=0)

    gift = relationship("InKindGift", back_populates="lines")
    debit_account = relationship("Account", foreign_keys=[debit_account_id])
    credit_account = relationship("Account", foreign_keys=[credit_account_id])
