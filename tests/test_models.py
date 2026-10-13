"""
Tests for MediBridge database models.

Tests all constraints, relationships, and behaviors defined in the contract.
Uses in-memory SQLite with the same pragmas as production.
"""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from medibridge_db.database import Base, _apply_sqlite_pragmas, UTCDateTime
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
    SOSBroadcast,
    SOSResponse,
    # Enums
    UserRole,
    PharmacyVerification,
    StaffRoleAtPharmacy,
    StockStatus,
    ProposalStatus,
    HistoryChangeType,
    SubstitutionStatus,
    SosStatus,
    AliasType,
    UpdateSource,
)
from medibridge_db.normalize import normalize_composition
from medibridge_db.config import get_stale_threshold_hours


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
    )
    test_session.add(pharmacy)
    test_session.commit()
    return pharmacy


@pytest.fixture
def sample_user(test_session, sample_pharmacy):
    """Create a sample chemist user linked to pharmacy."""
    user = User(
        telegram_user_id=987654321,
        display_name="Test Chemist",
        role=UserRole.chemist,
    )
    test_session.add(user)
    test_session.commit()

    # Link user to pharmacy
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
    """Create a sample medicine (brand)."""
    from medibridge_db.normalize import normalize_composition
    medicine = Medicine(
        brand_name="Dolo 650",
        generic_name="Paracetamol",
        composition_key=normalize_composition("Paracetamol"),
        strength="650mg",
        form="tablet",
        is_schedule_h=False,
        manufacturer="Micro Labs",
    )
    test_session.add(medicine)
    test_session.commit()
    return medicine


@pytest.fixture
def sample_medicine2(test_session):
    """Create a second medicine with same composition (Calpol 650)."""
    from medibridge_db.normalize import normalize_composition
    medicine = Medicine(
        brand_name="Calpol 650",
        generic_name="Paracetamol",
        composition_key=normalize_composition("Paracetamol"),
        strength="650mg",
        form="tablet",
        is_schedule_h=False,
        manufacturer="GSK",
    )
    test_session.add(medicine)
    test_session.commit()
    return medicine


@pytest.fixture
def sample_inventory(test_session, sample_pharmacy, sample_medicine):
    """Create a sample inventory entry."""
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


# =============================================================================
# Normalization Tests
# =============================================================================

class TestNormalizeComposition:
    """Tests for composition key normalization."""

    def test_single_salt(self):
        assert normalize_composition("Paracetamol") == "paracetamol"

    def test_two_salts(self):
        assert normalize_composition("Paracetamol + Caffeine") == "caffeine+paracetamol"

    def test_three_salts(self):
        assert normalize_composition("A + B + C") == "a+b+c"

    def test_whitespace_handling(self):
        assert normalize_composition("  Paracetamol  +  Caffeine  ") == "caffeine+paracetamol"

    def test_different_order_same_result(self):
        assert normalize_composition("Caffeine + Paracetamol") == "caffeine+paracetamol"

    def test_case_insensitive(self):
        assert normalize_composition("PARACETAMOL + caffeine") == "caffeine+paracetamol"

    def test_combination_medicine(self):
        assert normalize_composition("Amoxicillin + Clavulanic Acid") == "amoxicillin+clavulanic acid"

    def test_empty_string(self):
        assert normalize_composition("") == ""

    def test_different_salts_different_keys(self):
        key1 = normalize_composition("Paracetamol")
        key2 = normalize_composition("Ibuprofen")
        assert key1 != key2


# =============================================================================
# Pharmacy Tests
# =============================================================================

class TestPharmacy:
    """Tests for Pharmacy model."""

    def test_create_pharmacy(self, test_session):
        pharmacy = Pharmacy(
            name="Test Pharmacy",
            locality="Delhi",
            address="123 Test St",
            latitude=28.6139,
            longitude=77.2090,
        )
        test_session.add(pharmacy)
        test_session.commit()
        assert pharmacy.id is not None
        assert pharmacy.is_active is True
        assert pharmacy.verification_state == PharmacyVerification.pending

    def test_telegram_chat_id_unique(self, test_session, sample_pharmacy):
        """Duplicate telegram_chat_id should be rejected."""
        pharmacy2 = Pharmacy(
            name="Test Pharmacy 2",
            locality="Delhi",
            address="456 Test St",
            latitude=28.6200,
            longitude=77.2100,
            telegram_chat_id=sample_pharmacy.telegram_chat_id,  # Same chat_id
        )
        test_session.add(pharmacy2)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "UNIQUE constraint failed" in str(exc_info.value) or "unique constraint" in str(exc_info.value).lower()

    def test_null_telegram_chat_id_allowed_multiple(self, test_session, sample_pharmacy):
        """Multiple pharmacies with NULL telegram_chat_id should be allowed."""
        pharmacy2 = Pharmacy(
            name="Test Pharmacy 2",
            locality="Delhi",
            address="456 Test St",
            latitude=28.6200,
            longitude=77.2100,
            telegram_chat_id=None,
        )
        test_session.add(pharmacy2)
        test_session.commit()
        assert pharmacy2.id is not None

    def test_latitude_bounds(self, test_session):
        """Latitude must be in [-90, 90]."""
        pharmacy = Pharmacy(
            name="Test",
            locality="Delhi",
            address="123 St",
            latitude=91,  # Invalid
            longitude=77.2090,
        )
        test_session.add(pharmacy)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_pharmacies_latitude" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_longitude_bounds(self, test_session):
        """Longitude must be in [-180, 180]."""
        pharmacy = Pharmacy(
            name="Test",
            locality="Delhi",
            address="123 St",
            latitude=28.6139,
            longitude=181,  # Invalid
        )
        test_session.add(pharmacy)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_pharmacies_longitude" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)


# =============================================================================
# User Tests
# =============================================================================

class TestUser:
    """Tests for User model."""

    def test_create_user(self, test_session):
        user = User(
            telegram_user_id=111222333,
            display_name="Test User",
            role=UserRole.patient,
        )
        test_session.add(user)
        test_session.commit()
        assert user.id is not None
        assert user.is_active is True


# =============================================================================
# UserPharmacy Tests
# =============================================================================

class TestUserPharmacy:
    """Tests for UserPharmacy many-to-many relationship."""

    def test_one_user_two_pharmacies(self, test_session, sample_user, sample_pharmacy):
        """One user can be linked to multiple pharmacies."""
        pharmacy2 = Pharmacy(
            name="Pharmacy 2",
            locality="Delhi",
            address="456 St",
            latitude=28.6200,
            longitude=77.2100,
        )
        test_session.add(pharmacy2)
        test_session.commit()

        # sample_user is already linked to sample_pharmacy via fixture
        # Link same user to pharmacy2
        link2 = UserPharmacy(
            user_id=sample_user.id,
            pharmacy_id=pharmacy2.id,
            role_at_pharmacy=StaffRoleAtPharmacy.pharmacist,
        )
        test_session.add(link2)
        test_session.commit()

        # Verify both links exist
        links = test_session.execute(
            select(UserPharmacy).where(UserPharmacy.user_id == sample_user.id)
        ).scalars().all()
        assert len(links) == 2
        pharmacy_ids = {link.pharmacy_id for link in links}
        assert sample_pharmacy.id in pharmacy_ids
        assert pharmacy2.id in pharmacy_ids

    def test_composite_pk_prevents_duplicate_link(self, test_session, sample_user, sample_pharmacy):
        """Same user-pharmacy pair cannot be linked twice."""
        # sample_user is already linked to sample_pharmacy via fixture
        # Try to create a duplicate link
        link2 = UserPharmacy(
            user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
            role_at_pharmacy=StaffRoleAtPharmacy.pharmacist,  # Different role, but same PK
        )
        test_session.add(link2)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "UNIQUE constraint failed" in str(exc_info.value) or "unique constraint" in str(exc_info.value).lower()


# =============================================================================
# Medicine Tests
# =============================================================================

class TestMedicine:
    """Tests for Medicine model."""

    def test_create_medicine(self, test_session):
        from medibridge_db.normalize import normalize_composition
        med = Medicine(
            brand_name="Test Medicine",
            generic_name="Test Salt",
            composition_key=normalize_composition("Test Salt"),
            strength="500mg",
            form="tablet",
        )
        test_session.add(med)
        test_session.commit()
        assert med.id is not None
        assert med.is_active is True
        assert med.is_schedule_h is False

    def test_same_brand_different_manufacturer_allowed(self, test_session, sample_medicine):
        """Different brand with same composition but different manufacturer is allowed."""
        # This should work - different brand_name
        med2 = Medicine(
            brand_name="Different Brand",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="650mg",
            form="tablet",
            manufacturer="Another Corp",
        )
        test_session.add(med2)
        test_session.commit()
        assert med2.id is not None

    def test_same_composition_different_brand_allowed(self, test_session, sample_medicine, sample_medicine2):
        """Two different brands with same composition_key, strength, form ARE allowed."""
        # Both Dolo 650 and Calpol 650 should exist
        meds = test_session.execute(
            select(Medicine).where(
                Medicine.composition_key == "paracetamol",
                Medicine.strength == "650mg",
                Medicine.form == "tablet",
            )
        ).scalars().all()
        assert len(meds) == 2
        brands = {m.brand_name for m in meds}
        assert "Dolo 650" in brands
        assert "Calpol 650" in brands

    def test_exact_duplicate_brand_rejected(self, test_session, sample_medicine):
        """Exact duplicate (same brand, composition, strength, form, manufacturer) rejected."""
        duplicate = Medicine(
            brand_name=sample_medicine.brand_name,
            generic_name=sample_medicine.generic_name,
            composition_key=sample_medicine.composition_key,
            strength=sample_medicine.strength,
            form=sample_medicine.form,
            manufacturer=sample_medicine.manufacturer,
        )
        test_session.add(duplicate)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "uq_medicines_brand_composition" in str(exc_info.value) or "UNIQUE constraint failed" in str(exc_info.value)

    def test_null_manufacturer_normalized(self, test_session):
        """NULL manufacturer allows duplicates (SQLite UNIQUE treats NULL as distinct)."""
        from medibridge_db.normalize import normalize_composition
        med1 = Medicine(
            brand_name="No Manufacturer Brand",
            generic_name="Test Salt",
            composition_key=normalize_composition("Test Salt"),
            strength="100mg",
            form="tablet",
            manufacturer=None,
        )
        test_session.add(med1)
        test_session.commit()

        # Another with same fields and NULL manufacturer is allowed by SQLite
        # (UNIQUE constraint treats NULL as distinct values)
        med2 = Medicine(
            brand_name="No Manufacturer Brand",
            generic_name="Test Salt",
            composition_key=normalize_composition("Test Salt"),
            strength="100mg",
            form="tablet",
            manufacturer=None,
        )
        test_session.add(med2)
        test_session.commit()
        assert med2.id is not None

        # But empty string manufacturer should enforce uniqueness
        med3 = Medicine(
            brand_name="Empty Manufacturer Brand",
            generic_name="Test Salt 2",
            composition_key=normalize_composition("Test Salt 2"),
            strength="200mg",
            form="tablet",
            manufacturer="",
        )
        test_session.add(med3)
        test_session.commit()

        med4 = Medicine(
            brand_name="Empty Manufacturer Brand",
            generic_name="Test Salt 2",
            composition_key=normalize_composition("Test Salt 2"),
            strength="200mg",
            form="tablet",
            manufacturer="",
        )
        test_session.add(med4)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "uq_medicines_brand_composition" in str(exc_info.value) or "UNIQUE constraint failed" in str(exc_info.value)


# =============================================================================
# MedicineAlias Tests
# =============================================================================

class TestMedicineAlias:
    """Tests for MedicineAlias model."""

    def test_create_alias(self, test_session, sample_medicine):
        alias = MedicineAlias(
            medicine_id=sample_medicine.id,
            alias="dolo650",
            alias_type=AliasType.short,
        )
        test_session.add(alias)
        test_session.commit()
        assert alias.id is not None

    def test_alias_unique(self, test_session, sample_medicine):
        """Each alias must be unique across all medicines."""
        alias1 = MedicineAlias(
            medicine_id=sample_medicine.id,
            alias="uniquealias",
            alias_type=AliasType.short,
        )
        test_session.add(alias1)
        test_session.commit()

        # Create another medicine
        from medibridge_db.normalize import normalize_composition
        med2 = Medicine(
            brand_name="Other Brand",
            generic_name="Other Salt",
            composition_key=normalize_composition("Other Salt"),
            strength="100mg",
            form="tablet",
        )
        test_session.add(med2)
        test_session.commit()

        alias2 = MedicineAlias(
            medicine_id=med2.id,
            alias="uniquealias",  # Same alias
            alias_type=AliasType.short,
        )
        test_session.add(alias2)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "UNIQUE constraint failed" in str(exc_info.value) or "unique constraint" in str(exc_info.value).lower()


# =============================================================================
# Inventory Tests
# =============================================================================

class TestInventory:
    """Tests for Inventory model."""

    def test_create_inventory(self, test_session, sample_pharmacy, sample_medicine):
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inv)
        test_session.commit()
        assert inv.id is not None
        assert inv.stock_status == StockStatus.in_stock
        assert inv.quantity == 50

    def test_nonexistent_pharmacy_rejected(self, test_session, sample_medicine):
        """Inventory for nonexistent pharmacy should be rejected."""
        inv = Inventory(
            pharmacy_id=99999,  # Doesn't exist
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()

    def test_nonexistent_medicine_rejected(self, test_session, sample_pharmacy):
        """Inventory for nonexistent medicine should be rejected."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=99999,  # Doesn't exist
            stock_status=StockStatus.in_stock,
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()

    def test_duplicate_inventory_rejected(self, test_session, sample_inventory):
        """Duplicate inventory for same pharmacy+medicine rejected."""
        inv2 = Inventory(
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            stock_status=StockStatus.out_of_stock,
        )
        test_session.add(inv2)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "uq_inventory_pharmacy_medicine" in str(exc_info.value) or "UNIQUE constraint failed" in str(exc_info.value)

    def test_negative_quantity_rejected(self, test_session, sample_pharmacy, sample_medicine):
        """Negative quantity should be rejected."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=-1,  # Invalid
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_inventory_quantity_nonneg" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_out_of_stock_with_positive_quantity_rejected(self, test_session, sample_pharmacy, sample_medicine):
        """OUT_OF_STOCK with quantity > 0 should be rejected."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.out_of_stock,
            quantity=10,  # Contradicts OUT_OF_STOCK
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_inventory_status_quantity" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_in_stock_with_zero_quantity_rejected(self, test_session, sample_pharmacy, sample_medicine):
        """IN_STOCK with quantity = 0 should be rejected."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=0,  # Contradicts IN_STOCK
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_inventory_status_quantity" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_low_stock_with_zero_quantity_rejected(self, test_session, sample_pharmacy, sample_medicine):
        """LOW_STOCK with quantity = 0 should be rejected."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.low_stock,
            quantity=0,  # Contradicts LOW_STOCK
        )
        test_session.add(inv)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_inventory_status_quantity" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_out_of_stock_with_null_quantity_allowed(self, test_session, sample_pharmacy, sample_medicine):
        """OUT_OF_STOCK with NULL quantity should be allowed (unknown qty)."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.out_of_stock,
            quantity=None,
        )
        test_session.add(inv)
        test_session.commit()
        assert inv.id is not None

    def test_in_stock_with_null_quantity_allowed(self, test_session, sample_pharmacy, sample_medicine):
        """IN_STOCK with NULL quantity should be allowed (unknown qty)."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=None,
        )
        test_session.add(inv)
        test_session.commit()
        assert inv.id is not None

    def test_is_stale_true(self, test_session, sample_pharmacy, sample_medicine):
        """Inventory older than threshold is stale."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            last_confirmed_at=old_time,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inv)
        test_session.commit()
        assert inv.is_stale(threshold_hours=24) is True

    def test_is_stale_false(self, test_session, sample_pharmacy, sample_medicine):
        """Inventory newer than threshold is not stale."""
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            last_confirmed_at=datetime.now(timezone.utc) - timedelta(hours=1),
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inv)
        test_session.commit()
        assert inv.is_stale(threshold_hours=24) is False


# =============================================================================
# StockProposal Tests
# =============================================================================

class TestStockProposal:
    """Tests for StockProposal model."""

    def test_create_proposal(self, test_session, sample_pharmacy, sample_user):
        proposal = StockProposal(
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed_salt="Paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_intent=StockStatus.out_of_stock,
            confidence=0.9,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        test_session.add(proposal)
        test_session.commit()
        assert proposal.id is not None
        assert proposal.status == ProposalStatus.pending
        assert proposal.flagged_for_review is False

    def test_confidence_bounds(self, test_session, sample_pharmacy, sample_user):
        """Confidence must be in [0, 1]."""
        proposal = StockProposal(
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="test",
            parsed_salt="Test",
            parsed_strength="100mg",
            parsed_intent=StockStatus.in_stock,
            confidence=1.5,  # Invalid
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        test_session.add(proposal)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_proposals_confidence" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_negative_parsed_quantity_rejected(self, test_session, sample_pharmacy, sample_user):
        """Negative parsed_quantity should be rejected."""
        proposal = StockProposal(
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="test",
            parsed_salt="Test",
            parsed_strength="100mg",
            parsed_intent=StockStatus.in_stock,
            confidence=0.9,
            parsed_quantity=-1,  # Invalid
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        test_session.add(proposal)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_proposals_quantity_nonneg" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)

    def test_source_message_ref_grouping(self, test_session, sample_pharmacy, sample_user):
        """Two proposals with same source_message_ref should be retrievable together."""
        ref = "msg-group-123"

        prop1 = StockProposal(
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed_salt="Paracetamol",
            parsed_strength="650mg",
            parsed_intent=StockStatus.out_of_stock,
            confidence=0.9,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            source_message_ref=ref,
        )
        prop2 = StockProposal(
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Crocin 500 khatam",
            parsed_salt="Paracetamol",
            parsed_strength="500mg",
            parsed_intent=StockStatus.out_of_stock,
            confidence=0.85,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            source_message_ref=ref,
        )
        test_session.add_all([prop1, prop2])
        test_session.commit()

        # Query by source_message_ref
        proposals = test_session.execute(
            select(StockProposal).where(StockProposal.source_message_ref == ref)
        ).scalars().all()
        assert len(proposals) == 2


# =============================================================================
# StockHistory Tests
# =============================================================================

class TestStockHistory:
    """Tests for StockHistory model and constraints."""

    def test_create_history(self, test_session, sample_inventory, sample_user):
        history = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
            change_reason="test",
        )
        test_session.add(history)
        test_session.commit()
        assert history.id is not None
        assert history.change_type == HistoryChangeType.stock_update
        assert history.undo_of_history_id is None

    def test_undo_entry_cannot_be_undone_enforced_in_operations(self, test_session, sample_inventory, sample_user):
        """
        Test that an undo entry (change_type=UNDO) cannot be undone.
        Note: This constraint is documented and enforced in operations layer,
        not at DB level (requires cross-row check).
        """
        # First create a STOCK_UPDATE entry
        h1 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(h1)
        test_session.commit()

        # Create an UNDO entry pointing to h1
        h2 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.undo,
            undo_of_history_id=h1.id,
            new_stock_status=StockStatus.in_stock,
            new_quantity=50,
            previous_stock_status=StockStatus.out_of_stock,
            previous_quantity=0,
        )
        test_session.add(h2)
        test_session.commit()

        # Try to create another UNDO pointing to h2 (undo of undo)
        # This should be rejected by operations layer, not DB
        h3 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.undo,
            undo_of_history_id=h2.id,  # Trying to undo an undo
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(h3)
        test_session.commit()  # DB allows this; operations layer must prevent

        # Verify both exist (DB doesn't prevent undo of undo)
        undos = test_session.execute(
            select(StockHistory).where(StockHistory.undo_of_history_id.isnot(None))
        ).scalars().all()
        assert len(undos) == 2

    def test_undo_of_history_id_unique(self, test_session, sample_inventory, sample_user):
        """Each history entry can be undone at most once (DB constraint)."""
        h1 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(h1)
        test_session.commit()

        # First undo
        h2 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.undo,
            undo_of_history_id=h1.id,
            new_stock_status=StockStatus.in_stock,
            new_quantity=50,
            previous_stock_status=StockStatus.out_of_stock,
            previous_quantity=0,
        )
        test_session.add(h2)
        test_session.commit()

        # Second undo of same h1 should fail
        h3 = StockHistory(
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            changed_by=sample_user.id,
            change_type=HistoryChangeType.undo,
            undo_of_history_id=h1.id,  # Same target
            new_stock_status=StockStatus.in_stock,
            new_quantity=50,
            previous_stock_status=StockStatus.out_of_stock,
            previous_quantity=0,
        )
        test_session.add(h3)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "uq_history_undo_of" in str(exc_info.value) or "UNIQUE constraint failed" in str(exc_info.value)

    def test_history_survives_pharmacy_delete(self, test_session):
        """Deleting a pharmacy does not cascade-delete history (RESTRICT)."""
        # Create fresh pharmacy, medicine, user without links
        pharmacy = Pharmacy(
            name="Test Pharmacy",
            locality="Delhi",
            address="123 Test St",
            latitude=28.6139,
            longitude=77.2090,
        )
        test_session.add(pharmacy)
        test_session.commit()

        medicine = Medicine(
            brand_name="Test Med",
            generic_name="Test Salt",
            composition_key="test salt",
            strength="100mg",
            form="tablet",
        )
        test_session.add(medicine)
        test_session.commit()

        user = User(
            telegram_user_id=111222333,
            display_name="Test User",
            role=UserRole.chemist,
        )
        test_session.add(user)
        test_session.commit()

        inventory = Inventory(
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inventory)
        test_session.commit()

        history = StockHistory(
            inventory_id=inventory.id,
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            changed_by=user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(history)
        test_session.commit()

        # Try to delete pharmacy - should fail due to RESTRICT on stock_history.pharmacy_id
        test_session.delete(pharmacy)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()

    def test_history_survives_medicine_delete(self, test_session):
        """Deleting a medicine does not cascade-delete history (RESTRICT)."""
        # Create fresh pharmacy, medicine, user without links
        pharmacy = Pharmacy(
            name="Test Pharmacy 2",
            locality="Delhi",
            address="123 Test St",
            latitude=28.6139,
            longitude=77.2090,
        )
        test_session.add(pharmacy)
        test_session.commit()

        medicine = Medicine(
            brand_name="Test Med 2",
            generic_name="Test Salt 2",
            composition_key="test salt 2",
            strength="100mg",
            form="tablet",
        )
        test_session.add(medicine)
        test_session.commit()

        user = User(
            telegram_user_id=222333444,
            display_name="Test User 2",
            role=UserRole.chemist,
        )
        test_session.add(user)
        test_session.commit()

        inventory = Inventory(
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inventory)
        test_session.commit()

        history = StockHistory(
            inventory_id=inventory.id,
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            changed_by=user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(history)
        test_session.commit()

        # Try to delete medicine - should fail due to RESTRICT on stock_history.medicine_id
        test_session.delete(medicine)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()

    def test_history_survives_user_delete(self, test_session):
        """Deleting a user does not cascade-delete history (RESTRICT)."""
        # Create fresh pharmacy, medicine, user without links
        pharmacy = Pharmacy(
            name="Test Pharmacy 3",
            locality="Delhi",
            address="123 Test St",
            latitude=28.6139,
            longitude=77.2090,
        )
        test_session.add(pharmacy)
        test_session.commit()

        medicine = Medicine(
            brand_name="Test Med 3",
            generic_name="Test Salt 3",
            composition_key="test salt 3",
            strength="100mg",
            form="tablet",
        )
        test_session.add(medicine)
        test_session.commit()

        user = User(
            telegram_user_id=333444555,
            display_name="Test User 3",
            role=UserRole.chemist,
        )
        test_session.add(user)
        test_session.commit()

        inventory = Inventory(
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inventory)
        test_session.commit()

        history = StockHistory(
            inventory_id=inventory.id,
            pharmacy_id=pharmacy.id,
            medicine_id=medicine.id,
            changed_by=user.id,
            change_type=HistoryChangeType.stock_update,
            new_stock_status=StockStatus.out_of_stock,
            new_quantity=0,
            previous_stock_status=StockStatus.in_stock,
            previous_quantity=50,
        )
        test_session.add(history)
        test_session.commit()

        # Try to delete user - should fail due to RESTRICT on stock_history.changed_by
        test_session.delete(user)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "FOREIGN KEY constraint failed" in str(exc_info.value) or "foreign key" in str(exc_info.value).lower()


# =============================================================================
# SubstitutionApproval Tests
# =============================================================================

class TestSubstitutionApproval:
    """Tests for SubstitutionApproval model."""

    def test_create_approval(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_medicine2):
        approval = SubstitutionApproval(
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
            substitute_medicine_id=sample_medicine2.id,
            approving_pharmacist_id=sample_user.id,
            status=SubstitutionStatus.approved,
        )
        test_session.add(approval)
        test_session.commit()
        assert approval.id is not None
        assert approval.status == SubstitutionStatus.approved

    def test_same_medicine_rejected(self, test_session, sample_pharmacy, sample_user, sample_medicine):
        """Requested and substitute must be different medicines."""
        approval = SubstitutionApproval(
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
            substitute_medicine_id=sample_medicine.id,  # Same!
            approving_pharmacist_id=sample_user.id,
            status=SubstitutionStatus.approved,
        )
        test_session.add(approval)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "ck_sub_approvals_diff_med" in str(exc_info.value) or "CHECK constraint failed" in str(exc_info.value)


# =============================================================================
# Timezone Tests
# =============================================================================

class TestTimezoneHandling:
    """Tests for UTC timezone handling."""

    def test_naive_datetime_rejected(self, test_session):
        """Naive datetime should be rejected on write via ORM."""
        # Try to insert naive datetime via ORM - should raise ValueError
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)  # No timezone
        pharmacy = Pharmacy(
            name="TZ Test",
            locality="Delhi",
            address="123 St",
            latitude=28.6139,
            longitude=77.2090,
            created_at=naive_dt,
            updated_at=naive_dt,
        )
        test_session.add(pharmacy)
        with pytest.raises(Exception) as exc_info:
            test_session.commit()
        assert "Naive datetime is not allowed" in str(exc_info.value)

    def test_aware_datetime_read_back(self, test_session, sample_pharmacy):
        """Timezone-aware datetime read back should still be aware UTC."""
        # Pharmacy was created with utc_now() which is aware
        assert sample_pharmacy.created_at.tzinfo is not None
        assert sample_pharmacy.created_at.tzinfo == timezone.utc

    def test_non_utc_converted_to_utc(self, test_session):
        """Non-UTC timezone should be converted to UTC on write."""
        # Create with IST (UTC+5:30)
        ist = timezone(timedelta(hours=5, minutes=30))
        dt_ist = datetime(2026, 1, 1, 12, 0, 0, tzinfo=ist)

        # Use ORM to insert
        pharmacy = Pharmacy(
            name="TZ Test",
            locality="Delhi",
            address="123 St",
            latitude=28.6139,
            longitude=77.2090,
            created_at=dt_ist,
            updated_at=dt_ist,
        )
        test_session.add(pharmacy)
        test_session.commit()

        # Expire and reload to get the value from DB (since test fixture uses expire_on_commit=False)
        test_session.expire(pharmacy)
        loaded = test_session.get(Pharmacy, pharmacy.id)
        assert loaded.created_at.tzinfo == timezone.utc
        # 12:00 IST = 06:30 UTC
        assert loaded.created_at.hour == 6
        assert loaded.created_at.minute == 30


# =============================================================================
# Staleness Tests
# =============================================================================

class TestStaleness:
    """Tests for inventory staleness computation."""

    def test_staleness_uses_config_threshold(self, test_session, sample_pharmacy, sample_medicine):
        """is_stale() uses get_stale_threshold_hours() from config by default."""
        # Create inventory with 25h old timestamp
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            last_confirmed_at=old_time,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inv)
        test_session.commit()

        # Default threshold is 24h from config
        assert inv.is_stale() is True

    def test_staleness_custom_threshold(self, test_session, sample_pharmacy, sample_medicine):
        """is_stale() accepts custom threshold."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        inv = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            last_confirmed_at=old_time,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inv)
        test_session.commit()

        # With 48h threshold, not stale
        assert inv.is_stale(threshold_hours=48) is False


# =============================================================================
# Substitute Lookup Tests
# =============================================================================

class TestSubstituteLookup:
    """Tests for substitute lookup via composition_key."""

    def test_substitute_query_returns_multiple_brands(self, test_session, sample_pharmacy, sample_medicine, sample_medicine2):
        """Substitute lookup returns all brands with same composition_key, strength, form."""
        # Both Dolo 650 and Calpol 650 have composition_key='paracetamol', strength='650mg', form='tablet'
        results = test_session.execute(
            select(Medicine).where(
                Medicine.composition_key == "paracetamol",
                Medicine.strength == "650mg",
                Medicine.form == "tablet",
                Medicine.is_active == True,
            )
        ).scalars().all()
        assert len(results) == 2
        brands = {m.brand_name for m in results}
        assert "Dolo 650" in brands
        assert "Calpol 650" in brands

    def test_substitute_query_excludes_out_of_stock_at_pharmacy(self, test_session, sample_pharmacy, sample_medicine, sample_medicine2):
        """When pharmacy_id given, only in-stock substitutes returned."""
        # Add inventory for both medicines
        inv1 = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=10,
            last_update_source=UpdateSource.seed,
        )
        inv2 = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine2.id,
            stock_status=StockStatus.out_of_stock,
            quantity=0,
            last_update_source=UpdateSource.seed,
        )
        test_session.add_all([inv1, inv2])
        test_session.commit()

        # Query with pharmacy_id - should only return in_stock
        from sqlalchemy import not_
        results = test_session.execute(
            select(Medicine)
            .join(Inventory, (Inventory.medicine_id == Medicine.id) & (Inventory.pharmacy_id == sample_pharmacy.id))
            .where(
                Medicine.composition_key == "paracetamol",
                Medicine.strength == "650mg",
                Medicine.form == "tablet",
                Medicine.is_active == True,
                not_(Inventory.stock_status == StockStatus.out_of_stock),
            )
        ).scalars().all()
        assert len(results) == 1
        assert results[0].brand_name == "Dolo 650"


# =============================================================================
# Init DB Test
# =============================================================================

class TestInitDB:
    """Test init_db creates all tables."""

    def test_init_db_creates_all_tables(self, test_engine):
        """Verify all tables are created."""
        from sqlalchemy import inspect
        inspector = inspect(test_engine)
        tables = set(inspector.get_table_names())

        expected = {
            "pharmacies", "users", "user_pharmacies", "medicines",
            "medicine_aliases", "inventory", "stock_proposals",
            "stock_history", "substitution_approvals",
            "sos_broadcasts", "sos_responses",
        }
        assert expected.issubset(tables)


# =============================================================================
# Working Import Path Test
# =============================================================================

class TestImportPath:
    """Test the working import path."""

    def test_import_from_medibridge_db(self):
        """Test that models can be imported from medibridge_db."""
        from medibridge_db.models import (
            Pharmacy, User, Medicine, Inventory,
            StockProposal, StockHistory, SubstitutionApproval,
        )
        from medibridge_db.normalize import normalize_composition as norm_func
        from medibridge_db.database import Base, get_session, init_db
        from medibridge_db.config import get_database_url, get_stale_threshold_hours

        # All imports succeed
        assert callable(norm_func)
        assert callable(get_session)
        assert callable(init_db)
        assert callable(get_database_url)
        assert callable(get_stale_threshold_hours)