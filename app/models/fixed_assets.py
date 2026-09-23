# ============================================================================
# Fixed assets — register + depreciation, designed from the joelmacklow
# fork's fixed-assets slice (docs/spec-fixed-assets-management.md).
#
# Asset types carry the three account mappings (asset, accumulated
# depreciation, depreciation expense) so the register never hardcodes
# account numbers. Book value is DERIVED (cost - accumulated), never
# stored as an editable field. Depreciation history rows are deferred
# per the spec — runs accumulate onto the asset and post journals.
# ============================================================================

import enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class FixedAssetStatus(str, enum.Enum):
    REGISTERED = "registered"
    DISPOSED = "disposed"


class DepreciationMethod(str, enum.Enum):
    STRAIGHT_LINE = "straight_line"
    DECLINING_BALANCE = "declining_balance"


class FixedAssetType(Base):
    __tablename__ = "fixed_asset_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    asset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    accumulated_depreciation_account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )
    depreciation_expense_account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )
    depreciation_method = Column(
        Enum(DepreciationMethod),
        nullable=False,
        default=DepreciationMethod.STRAIGHT_LINE,
    )
    # STRAIGHT_LINE uses effective life; DECLINING_BALANCE uses annual rate
    effective_life_years = Column(Numeric(8, 2), nullable=True)
    annual_rate = Column(Numeric(8, 4), nullable=True)  # e.g. 0.2000 = 20%/yr
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    assets = relationship("FixedAsset", back_populates="asset_type")


class FixedAsset(Base):
    __tablename__ = "fixed_assets"

    id = Column(Integer, primary_key=True, index=True)
    asset_number = Column(String(40), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    asset_type_id = Column(
        Integer, ForeignKey("fixed_asset_types.id"), nullable=False, index=True
    )
    status = Column(
        Enum(FixedAssetStatus), nullable=False, default=FixedAssetStatus.REGISTERED
    )
    purchase_date = Column(Date, nullable=False)
    purchase_price = Column(Numeric(15, 2), nullable=False, default=0)
    salvage_value = Column(Numeric(15, 2), nullable=False, default=0)
    description = Column(Text, nullable=True)

    # Maintained by depreciation runs / disposal — never edited directly.
    accumulated_depreciation = Column(Numeric(15, 2), nullable=False, default=0)
    last_depreciation_date = Column(Date, nullable=True)

    disposal_date = Column(Date, nullable=True)
    disposal_proceeds = Column(Numeric(15, 2), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    asset_type = relationship("FixedAssetType", back_populates="assets")
