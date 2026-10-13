"""
Shared pytest fixtures for MediBridge tests.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from medibridge_db.database import Base, _apply_sqlite_pragmas
from medibridge_db.models import (
    Pharmacy,
    User,
    UserPharmacy,
    Medicine,
    MedicineAlias,
    Inventory,
    StockProposal,
    StockHistory,
    SubstitutionApproval,
    UserRole,
    PharmacyVerification,
    StaffRoleAtPharmacy,
    StockStatus,
    ProposalStatus,
    HistoryChangeType,
    UpdateSource,
    SubstitutionStatus,
    AliasType,
)
from medibridge_db.normalize import normalize_composition


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def test_engine():
    """Create an in-memory test engine with production pragmas."""
    engine = create_engine("sqlite:///:memory:", future=True)
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session(test_engine):
    """Create a test session."""
    SessionLocal = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_pharmacy(test_session):
    """Create a sample pharmacy."""
    pharmacy = Pharmacy(
        name="Test Pharmacy",
        locality="Delhi",
        address="123 Test Street",
        latitude=28.6139,
        longitude=77.2090,
        telegram_chat_id=123456789,
        verification_state=PharmacyVerification.verified,
        is_active=True,
    )
    test_session.add(pharmacy)
    test_session.commit()
    return pharmacy


@pytest.fixture
def sample_pharmacy2(test_session):
    """Create a second sample pharmacy for pharmacy mismatch tests."""
    pharmacy = Pharmacy(
        name="Test Pharmacy 2",
        locality="Delhi",
        address="456 Test Street",
        latitude=28.6200,
        longitude=77.2100,
        telegram_chat_id=987654321,
        verification_state=PharmacyVerification.verified,
        is_active=True,
    )
    test_session.add(pharmacy)
    test_session.commit()
    return pharmacy


@pytest.fixture
def sample_user(test_session, sample_pharmacy):
    """Create a sample user linked to sample_pharmacy."""
    user = User(
        telegram_user_id=987654321,
        display_name="Test Chemist",
        role=UserRole.chemist,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()

    link = UserPharmacy(
        user_id=user.id,
        pharmacy_id=sample_pharmacy.id,
        role_at_pharmacy=StaffRoleAtPharmacy.chemist,
    )
    test_session.add(link)
    test_session.commit()
    return user


@pytest.fixture
def sample_medicine(test_session):
    """Create a sample medicine (Dolo 650)."""
    medicine = Medicine(
        brand_name="Dolo 650",
        generic_name="Paracetamol",
        composition_key=normalize_composition("Paracetamol"),
        strength="650mg",
        form="tablet",
        is_schedule_h=False,
        manufacturer="Micro Labs",
        is_active=True,
    )
    test_session.add(medicine)
    test_session.commit()
    return medicine


@pytest.fixture
def sample_medicine2(test_session):
    """Create a second medicine (Calpol 650) with same composition for substitute tests."""
    medicine = Medicine(
        brand_name="Calpol 650",
        generic_name="Paracetamol",
        composition_key=normalize_composition("Paracetamol"),
        strength="650mg",
        form="tablet",
        is_schedule_h=False,
        manufacturer="GSK",
        is_active=True,
    )
    test_session.add(medicine)
    test_session.commit()
    return medicine


@pytest.fixture
def sample_medicine3(test_session):
    """Create a third medicine (Crocin 650) with same composition."""
    medicine = Medicine(
        brand_name="Crocin 650",
        generic_name="Paracetamol",
        composition_key=normalize_composition("Paracetamol"),
        strength="650mg",
        form="tablet",
        is_schedule_h=False,
        manufacturer="GSK",
        is_active=True,
    )
    test_session.add(medicine)
    test_session.commit()
    return medicine


@pytest.fixture
def sample_inventory(test_session, sample_pharmacy, sample_medicine, sample_user):
    """Create a sample inventory row."""
    inventory = Inventory(
        pharmacy_id=sample_pharmacy.id,
        medicine_id=sample_medicine.id,
        stock_status=StockStatus.in_stock,
        quantity=50,
        last_update_source=UpdateSource.seed,
    )
    test_session.add(inventory)
    test_session.commit()
    return inventory


@pytest.fixture
def sample_proposal(test_session, sample_pharmacy, sample_user, sample_medicine):
    """Create a sample stock proposal."""
    proposal = StockProposal(
        pharmacy_id=sample_pharmacy.id,
        chemist_user_id=sample_user.id,
        raw_message="Dolo 650 khatam",
        brand_name="Dolo 650",
        parsed_salt="Paracetamol",
        parsed_strength="650mg",
        parsed_form="tablet",
        parsed_quantity=0,
        parsed_intent=StockStatus.out_of_stock,
        confidence=0.92,
        status=ProposalStatus.confirmed,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    test_session.add(proposal)
    test_session.commit()
    return proposal


from datetime import datetime, timezone, timedelta