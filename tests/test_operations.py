"""
Tests for MediBridge database operations.
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from medibridge_db.database import Base, _apply_sqlite_pragmas, utc_now
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
from medibridge_db import exceptions
from medibridge_db.operations import (
    get_pharmacies_nearby,
    get_inventory_for_medicine,
    get_pharmacy_inventory,
    get_substitutes_for_medicine,
    get_stale_inventory,
    find_medicine_by_alias,
    search_medicines,
    get_medicine_by_id,
    create_stock_proposal,
    get_proposal_by_id,
    list_pending_proposals,
    expire_overdue_proposals,
    renew_proposal,
    apply_confirmed_proposal,
    get_inventory_history,
    get_latest_history_entry,
    get_pharmacy_history,
    get_user_history,
    undo_last_change,
    can_undo,
    record_substitution_approval,
    get_substitution_approvals,
    is_user_linked_to_pharmacy,
    get_user_pharmacies,
    get_pharmacy_staff,
)


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
    """Create a second pharmacy."""
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
def sample_user2(test_session, sample_pharmacy2):
    """Create a second user linked to the second pharmacy."""
    user = User(
        telegram_user_id=111222333,
        display_name="Test Chemist 2",
        role=UserRole.chemist,
            )
    test_session.add(user)
    test_session.commit()

    link = UserPharmacy(
        user_id=user.id,
        pharmacy_id=sample_pharmacy2.id,
        role_at_pharmacy=StaffRoleAtPharmacy.chemist,
    )
    test_session.add(link)
    test_session.commit()
    return user


@pytest.fixture
def sample_medicine(test_session):
    """Create a sample medicine (brand)."""
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
        last_confirmed_at=utc_now(),
        last_update_source=UpdateSource.seed,
    )
    test_session.add(inventory)
    test_session.commit()
    return inventory


@pytest.fixture
def sample_inventory2(test_session, sample_pharmacy, sample_medicine2):
    """Create a second inventory entry for the same pharmacy, different medicine."""
    inventory = Inventory(
        pharmacy_id=sample_pharmacy.id,
        medicine_id=sample_medicine2.id,
        stock_status=StockStatus.in_stock,
        quantity=30,
        last_confirmed_at=utc_now(),
        last_update_source=UpdateSource.seed,
    )
    test_session.add(inventory)
    test_session.commit()
    return inventory


# =============================================================================
# Inventory Reads Tests
# =============================================================================

class TestInventoryReads:
    def test_get_pharmacies_nearby(self, test_session, sample_pharmacy, sample_pharmacy2):
        # Both pharmacies are active and verified
        # Sample pharmacy is at (28.6139, 77.2090)
        # Sample pharmacy 2 is at (28.6200, 77.2100) ~ 1km away
        # Search within 5km of sample pharmacy
        results = get_pharmacies_nearby(
            test_session,
            lat=sample_pharmacy.latitude,
            lon=sample_pharmacy.longitude,
            radius_km=5.0,
            limit=10,
        )
        assert len(results) == 2
        # The closest should be sample_pharmacy (distance 0)
        assert results[0].id == sample_pharmacy.id
        assert results[1].id == sample_pharmacy2.id

    def test_get_inventory_for_medicine(self, test_session, sample_inventory):
        inventory = get_inventory_for_medicine(
            test_session,
            pharmacy_id=sample_inventory.pharmacy_id,
            medicine_id=sample_inventory.medicine_id,
            include_stale=True,
            include_inactive=True,
        )
        assert inventory is not None
        assert inventory.id == sample_inventory.id
        assert inventory.quantity == 50

    def test_get_inventory_for_medicine_not_found(self, test_session, sample_pharmacy, sample_medicine):
        inventory = get_inventory_for_medicine(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            medicine_id=99999,  # Non-existent
            include_stale=True,
            include_inactive=True,
        )
        assert inventory is None

    def test_get_pharmacy_inventory(self, test_session, sample_inventory, sample_inventory2):
        inventory_list = get_pharmacy_inventory(
            test_session,
            pharmacy_id=sample_inventory.pharmacy_id,
            include_stale=True,
            status_filter=None,
            include_inactive=True,
        )
        assert len(inventory_list) == 2
        # Check that both inventories are present
        inventory_ids = {inv.id for inv in inventory_list}
        assert sample_inventory.id in inventory_ids
        assert sample_inventory2.id in inventory_ids

    def test_get_pharmacy_inventory_with_status_filter(self, test_session, sample_inventory, sample_inventory2):
        # Change one to out of stock
        sample_inventory2.stock_status = StockStatus.out_of_stock
        sample_inventory2.quantity = 0
        test_session.commit()

        inventory_list = get_pharmacy_inventory(
            test_session,
            pharmacy_id=sample_inventory.pharmacy_id,
            include_stale=True,
            status_filter=StockStatus.in_stock,
            include_inactive=True,
        )
        assert len(inventory_list) == 1
        assert inventory_list[0].id == sample_inventory.id

    def test_get_substitutes_for_medicine(self, test_session, sample_pharmacy, sample_medicine, sample_medicine2):
        # Create inventory for both medicines at the pharmacy
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
            stock_status=StockStatus.in_stock,
            quantity=20,
            last_update_source=UpdateSource.seed,
        )
        test_session.add_all([inv1, inv2])
        test_session.commit()

        # Get substitutes for medicine1 (should return medicine2)
        substitutes = get_substitutes_for_medicine(
            test_session,
            medicine_id=sample_medicine.id,
            pharmacy_id=sample_pharmacy.id,
            include_inactive=True,
        )
        assert len(substitutes) == 1
        assert substitutes[0].id == sample_medicine2.id

        # Get substitutes for medicine2 (should return medicine1)
        substitutes = get_substitutes_for_medicine(
            test_session,
            medicine_id=sample_medicine2.id,
            pharmacy_id=sample_pharmacy.id,
            include_inactive=True,
        )
        assert len(substitutes) == 1
        assert substitutes[0].id == sample_medicine.id

    def test_get_substitutes_excludes_out_of_stock(self, test_session, sample_pharmacy, sample_medicine, sample_medicine2):
        # Medicine1 in stock, medicine2 out of stock
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

        substitutes = get_substitutes_for_medicine(
            test_session,
            medicine_id=sample_medicine.id,
            pharmacy_id=sample_pharmacy.id,
            include_inactive=True,
        )
        assert len(substitutes) == 0  # medicine2 is out of stock

    def test_get_stale_inventory(self, test_session, sample_pharmacy, sample_medicine):
        # Create an inventory that is stale (older than 24h)
        old_time = utc_now() - timedelta(hours=25)
        inventory = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_confirmed_at=old_time,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inventory)
        test_session.commit()

        stale_list = get_stale_inventory(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            threshold_hours=24,
            include_inactive=True,
        )
        assert len(stale_list) == 1
        assert stale_list[0].id == inventory.id

    def test_get_stale_inventory_not_stale(self, test_session, sample_pharmacy, sample_medicine):
        # Create an inventory that is not stale (within 24h)
        recent_time = utc_now() - timedelta(hours=1)
        inventory = Inventory(
            pharmacy_id=sample_pharmacy.id,
            medicine_id=sample_medicine.id,
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_confirmed_at=recent_time,
            last_update_source=UpdateSource.seed,
        )
        test_session.add(inventory)
        test_session.commit()

        stale_list = get_stale_inventory(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            threshold_hours=24,
            include_inactive=True,
        )
        assert len(stale_list) == 0


# =============================================================================
# Medicine & Substitute Lookup Tests
# =============================================================================

class TestMedicineLookup:
    def test_find_medicine_by_alias(self, test_session, sample_medicine):
        alias = MedicineAlias(
            medicine_id=sample_medicine.id,
            alias="dolo650",
            alias_type=AliasType.short,
        )
        test_session.add(alias)
        test_session.commit()

        medicine = find_medicine_by_alias(
            test_session,
            alias="dolo650",
            include_inactive=True,
        )
        assert medicine is not None
        assert medicine.id == sample_medicine.id

    def test_search_medicines(self, test_session, sample_medicine, sample_medicine2):
        # Search for "paracetamol" should find both
        results = search_medicines(
            test_session,
            query="paracetamol",
            limit=10,
            include_inactive=True,
        )
        assert len(results) == 2
        medicine_names = {m.brand_name for m in results}
        assert "Dolo 650" in medicine_names
        assert "Calpol 650" in medicine_names

    def test_find_medicine_by_alias_literal_wildcard(self, test_session, sample_medicine):
        # Alias with % and _ should be treated literally, not as wildcards
        alias = MedicineAlias(
            medicine_id=sample_medicine.id,
            alias="dol%650_",  # literal percent and underscore
            alias_type=AliasType.short,
        )
        test_session.add(alias)
        test_session.commit()

        # Exact match should work
        medicine = find_medicine_by_alias(
            test_session,
            alias="dol%650_",
            include_inactive=True,
        )
        assert medicine is not None
        assert medicine.id == sample_medicine.id

        # Search for "dol" should NOT match because % and _ are literals
        medicine = find_medicine_by_alias(
            test_session,
            alias="dol",
            include_inactive=True,
        )
        assert medicine is None

    def test_get_medicine_by_id(self, test_session, sample_medicine):
        medicine = get_medicine_by_id(
            test_session,
            medicine_id=sample_medicine.id,
            include_inactive=True,
        )
        assert medicine is not None
        assert medicine.id == sample_medicine.id


# =============================================================================
# Proposals Tests
# =============================================================================

class TestProposals:
    def test_create_stock_proposal(self, test_session, sample_pharmacy, sample_user):
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "Paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
            "flagged_for_review": False,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        test_session.commit()
        assert proposal.id is not None
        assert proposal.status == ProposalStatus.pending
        assert proposal.flagged_for_review == False
        # Check expires_at is set to now + 24h
        assert proposal.expires_at >= utc_now() + timedelta(hours=23, minutes=55)

    def test_get_proposal_by_id(self, test_session, sample_pharmacy, sample_user):
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "Paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        test_session.commit()

        fetched = get_proposal_by_id(test_session, proposal.id)
        assert fetched is not None
        assert fetched.id == proposal.id

    def test_list_pending_proposals(self, test_session, sample_pharmacy, sample_user):
        # Create a pending proposal
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "Paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal1 = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # Create another pending proposal
        parsed2 = parsed.copy()
        parsed2["brand_name"] = "Crocin 500"
        parsed2["parsed_intent"] = StockStatus.in_stock
        proposal2 = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Crocin 500 available",
            parsed=parsed2,
        )
        test_session.commit()

        # List pending proposals for the pharmacy
        pending = list_pending_proposals(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            flagged_only=False,
            limit=10,
        )
        assert len(pending) == 2
        proposal_ids = {p.id for p in pending}
        assert proposal1.id in proposal_ids
        assert proposal2.id in proposal_ids

        # Test flagged_only
        # Flag one proposal
        proposal1.flagged_for_review = True
        test_session.commit()

        pending_flagged = list_pending_proposals(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            flagged_only=True,
            limit=10,
        )
        assert len(pending_flagged) == 1
        assert pending_flagged[0].id == proposal1.id

    def test_expire_overdue_proposals(self, test_session, sample_pharmacy, sample_user):
        # Create a proposal that is already expired
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "Paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # Manually set expires_at to the past
        proposal.expires_at = utc_now() - timedelta(hours=1)
        test_session.commit()

        # Run the expiration function
        expired_count = expire_overdue_proposals(
            test_session,
            cutoff=utc_now(),
        )
        assert expired_count == 1

        # Refresh and check status
        test_session.refresh(proposal)
        assert proposal.status == ProposalStatus.expired

    def test_renew_proposal(self, test_session, sample_pharmacy, sample_user):
        # Create an expired proposal
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "Paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        expired_proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # Set to expired
        expired_proposal.status = ProposalStatus.expired
        expired_proposal.expires_at = utc_now() - timedelta(hours=1)
        test_session.commit()

        # Renew it
        new_proposal = renew_proposal(
            test_session,
            expired_proposal_id=expired_proposal.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        test_session.commit()
        assert new_proposal.status == ProposalStatus.pending
        assert new_proposal.renewed_from_id == expired_proposal.id
        # Original should still be expired
        test_session.refresh(expired_proposal)
        assert expired_proposal.status == ProposalStatus.expired


# =============================================================================
# Apply Confirmed Proposal Tests
# =============================================================================

class TestApplyConfirmedProposal:
    def test_apply_confirmed_proposal_success(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Create a proposal
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,  # Out of stock
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
            "flagged_for_review": False,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # Confirm the proposal (simulating the service layer)
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        # Apply the proposal
        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Check that inventory was updated
        assert inventory.stock_status == StockStatus.out_of_stock
        assert inventory.quantity == 0
        assert inventory.last_updated_by == sample_user.id
        assert inventory.last_update_source == UpdateSource.proposal_apply

        # Check that proposal was updated with confirmed_by and confirmed_at
        test_session.refresh(proposal)
        assert proposal.confirmed_by == sample_user.id
        assert proposal.confirmed_at is not None

        # Check that a history row was created
        history = get_inventory_history(test_session, inventory.id, limit=1)
        assert len(history) == 1
        history_row = history[0]
        assert history_row.change_type == HistoryChangeType.stock_update
        assert history_row.source_proposal_id == proposal.id
        assert history_row.previous_stock_status == StockStatus.in_stock
        assert history_row.new_stock_status == StockStatus.out_of_stock
        assert history_row.previous_quantity == 50
        assert history_row.new_quantity == 0
        assert history_row.changed_by == sample_user.id

    def test_apply_confirmed_proposal_wrong_status(self, test_session, sample_pharmacy, sample_user, sample_medicine):
        # Create a proposal that is still pending
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # Leave it as PENDING
        test_session.commit()

        # Try to apply
        with pytest.raises(exceptions.ProposalNotPendingError):
            apply_confirmed_proposal(
                test_session,
                proposal_id=proposal.id,
                confirmed_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )

    def test_apply_confirmed_proposal_pharmacy_mismatch(self, test_session, sample_pharmacy, sample_pharmacy2, sample_user, sample_medicine):
        # Create a proposal for pharmacy1
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        # Try to apply with pharmacy2's ID
        with pytest.raises(exceptions.ProposalPharmacyMismatchError):
            apply_confirmed_proposal(
                test_session,
                proposal_id=proposal.id,
                confirmed_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy2.id,
            )

    def test_apply_confirmed_proposal_expired(self, test_session, sample_pharmacy, sample_user, sample_medicine):
        # Create a proposal
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        # Set expires_at to the past
        proposal.expires_at = utc_now() - timedelta(hours=1)
        test_session.commit()

        # Try to apply
        with pytest.raises(exceptions.ProposalExpiredError):
            apply_confirmed_proposal(
                test_session,
                proposal_id=proposal.id,
                confirmed_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
                current_time=utc_now(),
            )

    def test_apply_confirmed_proposal_inventory_not_found(self, test_session, sample_pharmacy, sample_user):
        # Create a proposal for a medicine that has no inventory
        parsed = {
            "brand_name": "Nonexistent",
            "parsed_salt": "Test",
            "parsed_strength": "100mg",
            "parsed_form": "tablet",
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Test khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        # Try to apply
        with pytest.raises(exceptions.InventoryNotFoundError):
            apply_confirmed_proposal(
                test_session,
                proposal_id=proposal.id,
                confirmed_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )


# =============================================================================
# History Reads Tests
# =============================================================================

class TestHistoryReads:
    def test_get_inventory_history(self, test_session, sample_inventory, sample_user):
        # Create a history row
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

        history_list = get_inventory_history(
            test_session,
            inventory_id=sample_inventory.id,
            limit=10,
        )
        assert len(history_list) == 1
        assert history_list[0].id == history.id

    def test_get_pharmacy_history(self, test_session, sample_pharmacy, sample_user, sample_inventory):
        # Create a history row
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

        history_list = get_pharmacy_history(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            limit=10,
        )
        assert len(history_list) == 1
        assert history_list[0].id == history.id

    def test_get_user_history(self, test_session, sample_user, sample_inventory):
        # Create a history row
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

        history_list = get_user_history(
            test_session,
            user_id=sample_user.id,
            limit=10,
        )
        assert len(history_list) == 1
        assert history_list[0].id == history.id

    def test_get_latest_history_entry(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Apply first change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal1 = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal1.status = ProposalStatus.confirmed
        test_session.commit()

        apply_confirmed_proposal(
            test_session,
            proposal_id=proposal1.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Apply second change (undo)
        history_list = get_inventory_history(test_session, sample_inventory.id, limit=1)
        first_history_id = history_list[0].id

        undo_last_change(
            test_session,
            history_id=first_history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get latest history entry
        latest = get_latest_history_entry(
            test_session,
            inventory_id=sample_inventory.id,
            pharmacy_id=sample_pharmacy.id,
        )
        assert latest is not None
        assert latest.change_type == HistoryChangeType.undo
        assert latest.undo_of_history_id == first_history_id

    def test_get_latest_history_entry_wrong_pharmacy(self, test_session, sample_pharmacy, sample_pharmacy2, sample_user, sample_medicine, sample_inventory):
        # Try to get history for inventory that belongs to sample_pharmacy but pass sample_pharmacy2
        with pytest.raises(exceptions.PharmacyScopeMismatchError):
            get_latest_history_entry(
                test_session,
                inventory_id=sample_inventory.id,
                pharmacy_id=sample_pharmacy2.id,
            )


# =============================================================================
# Undo Tests
# =============================================================================

class TestUndo:
    def test_undo_last_change_success(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # First, apply a change to create a history row to undo
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row that was just created (the latest)
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # Now undo it
        undo_history = undo_last_change(
            test_session,
            history_id=history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Check that inventory is restored
        test_session.refresh(inventory)
        assert inventory.stock_status == StockStatus.in_stock
        assert inventory.quantity == 50
        assert inventory.last_updated_by == sample_user.id
        assert inventory.last_update_source == UpdateSource.undo

        # Check that the undo history row was created
        assert undo_history.change_type == HistoryChangeType.undo
        assert undo_history.undo_of_history_id == history_id
        assert undo_history.previous_stock_status == StockStatus.out_of_stock  # current state before undo
        assert undo_history.new_stock_status == StockStatus.in_stock      # previous state
        assert undo_history.previous_quantity == 0
        assert undo_history.new_quantity == 50
        assert undo_history.changed_by == sample_user.id

        # Check that the original history row is unchanged (undo_of_history_id remains NULL)
        test_session.refresh(history_list[0])
        assert history_list[0].undo_of_history_id is None
        # And that an undo row exists pointing to it
        undo_stmt = select(StockHistory).where(StockHistory.undo_of_history_id == history_list[0].id)
        undo_row = test_session.execute(undo_stmt).scalar_one_or_none()
        assert undo_row is not None
        assert undo_row.id == undo_history.id

    def test_undo_last_change_already_undone(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Apply a change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # Undo it once
        undo_last_change(
            test_session,
            history_id=history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Try to undo again
        with pytest.raises(exceptions.HistoryAlreadyUndoneError):
            undo_last_change(
                test_session,
                history_id=history_id,
                undone_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )

    def test_undo_last_change_second_undo_rejected(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Apply a change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # First undo succeeds
        undo_history = undo_last_change(
            test_session,
            history_id=history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Second undo of same original history row should fail
        with pytest.raises(exceptions.HistoryAlreadyUndoneError):
            undo_last_change(
                test_session,
                history_id=history_id,
                undone_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )

    def test_undo_last_change_cannot_undo_undo(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Apply a change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # Undo it
        undo_history = undo_last_change(
            test_session,
            history_id=history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Try to undo the undo history row
        with pytest.raises(exceptions.CannotUndoUndoError):
            undo_last_change(
                test_session,
                history_id=undo_history.id,
                undone_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )

    def test_undo_last_change_newer_change_exists(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Apply first change
        parsed1 = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal1 = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed1,
        )
        proposal1.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal1.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Apply second change (newer)
        parsed2 = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 25,  # Low stock
            "parsed_intent": StockStatus.low_stock,
            "confidence": 0.9,
        }
        proposal2 = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 partial",
            parsed=parsed2,
        )
        proposal2.status = ProposalStatus.confirmed
        test_session.commit()

        apply_confirmed_proposal(
            test_session,
            proposal_id=proposal2.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the first history row (the one we want to undo)
        history_list = get_inventory_history(test_session, inventory.id, limit=2)
        # The first (older) is at index 1, the newer at index 0 (because we ordered by asc in get_inventory_history? Actually get_inventory_history returns oldest first)
        # Let's get all and order by id
        all_history = get_inventory_history(test_session, inventory.id, limit=10)
        # Sort by id to get oldest first
        all_history_sorted = sorted(all_history, key=lambda h: h.id)
        first_history = all_history_sorted[0]
        second_history = all_history_sorted[1]

        # Try to undo the first history row (which is not the latest)
        with pytest.raises(exceptions.NewerChangeExistsError):
            undo_last_change(
                test_session,
                history_id=first_history.id,
                undone_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy.id,
            )

    def test_undo_last_change_wrong_pharmacy(self, test_session, sample_pharmacy, sample_pharmacy2, sample_user, sample_medicine, sample_inventory):
        # Apply a change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # Try to undo with wrong pharmacy
        with pytest.raises(exceptions.PharmacyScopeMismatchError):
            undo_last_change(
                test_session,
                history_id=history_id,
                undone_by_user_id=sample_user.id,
                pharmacy_id=sample_pharmacy2.id,
            )

    def test_can_undo(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        # Initially, no history, so cannot undo
        assert can_undo(test_session, history_id=99999) is False

        # Apply a change
        parsed = {
            "brand_name": sample_medicine.brand_name,
            "parsed_salt": sample_medicine.generic_name,
            "parsed_strength": sample_medicine.strength,
            "parsed_form": sample_medicine.form,
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Get the history row
        history_list = get_inventory_history(test_session, inventory.id, limit=1)
        history_id = history_list[0].id

        # Should be able to undo
        assert can_undo(test_session, history_id=history_id) is True

        # After undoing, should not be able to undo again
        undo_last_change(
            test_session,
            history_id=history_id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()
        assert can_undo(test_session, history_id=history_id) is False


# =============================================================================
# Substitution Approvals Tests
# =============================================================================

class TestSubstitutionApprovals:
    def test_record_substitution_approval(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_medicine2):
        approval = record_substitution_approval(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
            substitute_medicine_id=sample_medicine2.id,
            approving_pharmacist_id=sample_user.id,
            status=SubstitutionStatus.approved,
            request_reference="Patient: John Doe",
        )
        test_session.commit()
        assert approval.id is not None
        assert approval.status == SubstitutionStatus.approved
        assert approval.request_reference == "Patient: John Doe"

    def test_record_substitution_approval_same_medicine_rejected(self, test_session, sample_pharmacy, sample_user, sample_medicine):
        with pytest.raises(ValueError, match="Requested and substitute medicines must be different"):
            record_substitution_approval(
                test_session,
                pharmacy_id=sample_pharmacy.id,
                requested_medicine_id=sample_medicine.id,
                substitute_medicine_id=sample_medicine.id,  # Same!
                approving_pharmacist_id=sample_user.id,
                status=SubstitutionStatus.approved,
            )

    def test_get_substitution_approvals(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_medicine2):
        # Create two approvals
        approval1 = record_substitution_approval(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
            substitute_medicine_id=sample_medicine2.id,
            approving_pharmacist_id=sample_user.id,
            status=SubstitutionStatus.approved,
            request_reference="First",
        )
        approval2 = record_substitution_approval(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
            substitute_medicine_id=sample_medicine2.id,
            approving_pharmacist_id=sample_user.id,
            status=SubstitutionStatus.rejected,
            request_reference="Second",
        )
        test_session.commit()

        # Get all approvals for the pharmacy
        approvals = get_substitution_approvals(
            test_session,
            pharmacy_id=sample_pharmacy.id,
        )
        assert len(approvals) == 2
        approval_ids = {a.id for a in approvals}
        assert approval1.id in approval_ids
        assert approval2.id in approval_ids

        # Get approvals for a specific requested medicine
        approvals = get_substitution_approvals(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=sample_medicine.id,
        )
        assert len(approvals) == 2  # Both are for the same requested medicine

        # Get approvals for a non-existent requested medicine
        approvals = get_substitution_approvals(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            requested_medicine_id=99999,
        )
        assert len(approvals) == 0


# =============================================================================
# Pharmacy Membership Reads Tests
# =============================================================================

class TestPharmacyMembership:
    def test_is_user_linked_to_pharmacy(self, test_session, sample_pharmacy, sample_user):
        assert is_user_linked_to_pharmacy(
            test_session,
            user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        ) is True

        # User not linked to pharmacy
        assert is_user_linked_to_pharmacy(
            test_session,
            user_id=sample_user.id,
            pharmacy_id=99999,
        ) is False

    def test_get_user_pharmacies(self, test_session, sample_pharmacy, sample_pharmacy2, sample_user, sample_user2):
        # User linked to one pharmacy
        pharmacies = get_user_pharmacies(
            test_session,
            user_id=sample_user.id,
            include_inactive=True,
        )
        assert len(pharmacies) == 1
        assert pharmacies[0].id == sample_pharmacy.id

        # User linked to two pharmacies (make sample_user2 also linked to sample_pharmacy)
        link = UserPharmacy(
            user_id=sample_user2.id,
            pharmacy_id=sample_pharmacy.id,
            role_at_pharmacy=StaffRoleAtPharmacy.chemist,
                    )
        test_session.add(link)
        test_session.commit()

        pharmacies = get_user_pharmacies(
            test_session,
            user_id=sample_user2.id,
            include_inactive=True,
        )
        assert len(pharmacies) == 2
        pharmacy_ids = {p.id for p in pharmacies}
        assert sample_pharmacy.id in pharmacy_ids
        assert sample_pharmacy2.id in pharmacy_ids

    def test_get_pharmacy_staff(self, test_session, sample_pharmacy, sample_user, sample_user2):
        # Pharmacy has one staff member (sample_user)
        staff = get_pharmacy_staff(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            role=StaffRoleAtPharmacy.chemist,
            include_inactive=True,
        )
        assert len(staff) == 1
        assert staff[0].id == sample_user.id

        # Add another staff member
        link2 = UserPharmacy(
            user_id=sample_user2.id,
            pharmacy_id=sample_pharmacy.id,
            role_at_pharmacy=StaffRoleAtPharmacy.pharmacist,
                    )
        test_session.add(link2)
        test_session.commit()

        staff = get_pharmacy_staff(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            role=StaffRoleAtPharmacy.chemist,
            include_inactive=True,
        )
        assert len(staff) == 1
        assert staff[0].id == sample_user.id

        staff = get_pharmacy_staff(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            role=None,  # All roles
            include_inactive=True,
        )
        assert len(staff) == 2
        staff_ids = {s.id for s in staff}
        assert sample_user.id in staff_ids
        assert sample_user2.id in staff_ids


# =============================================================================
# Workflow Test (from CONTRACT.md example)
# =============================================================================

class TestWorkflowExample:
    def test_workflow_example(self, test_session, sample_pharmacy, sample_user, sample_medicine, sample_inventory):
        """
        Test the exact workflow from the CONTRACT.md example:
        1. Propose
        2. Confirm & Apply
        3. Undo
        """
        # We need to set up the pharmacy and medicine as in the example
        # Example: pharmacy_id=5, medicine_id=12
        # We'll use our fixtures and adjust IDs if needed, but we can just use the objects.

        # 1. Propose
        parsed = {
            "brand_name": "Dolo 650",
            "parsed_salt": "paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": None,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
        }
        proposal = create_stock_proposal(
            test_session,
            pharmacy_id=sample_pharmacy.id,
            chemist_user_id=sample_user.id,
            raw_message="Dolo 650 khatam",
            parsed=parsed,
        )
        # DB writes: status=PENDING, expires_at=now+24h, flagged_for_review=0
        test_session.commit()
        assert proposal.status == ProposalStatus.pending
        assert proposal.flagged_for_review == False

        # 2. Confirm & Apply (Member 1 workflow)
        # In the example, the chemist confirms himself (confirmed_by_user_id=42)
        # We'll use sample_user.id as the confirmer
        proposal.status = ProposalStatus.confirmed
        test_session.commit()

        inventory = apply_confirmed_proposal(
            test_session,
            proposal_id=proposal.id,
            confirmed_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Check the DB writes as per example:
        # stock_history: id=201, ... change_type=STOCK_UPDATE, ...
        # inventory (id=7): stock_status=OUT_OF_STOCK, quantity=0, ...
        # stock_proposals (id=101): status=CONFIRMED, confirmed_by=42, confirmed_at=now
        history = get_inventory_history(test_session, inventory.id, limit=1)[0]
        assert history.change_type == HistoryChangeType.stock_update
        assert history.source_proposal_id == proposal.id
        assert history.previous_stock_status == StockStatus.in_stock
        assert history.new_stock_status == StockStatus.out_of_stock
        assert history.previous_quantity == 50  # from sample_inventory fixture
        assert history.new_quantity is None  # parsed_quantity was None in this proposal
        assert history.changed_by == sample_user.id
        assert inventory.stock_status == StockStatus.out_of_stock
        assert inventory.quantity is None  # parsed_quantity was None
        assert inventory.last_updated_by == sample_user.id
        assert inventory.last_update_source == UpdateSource.proposal_apply
        assert proposal.confirmed_by == sample_user.id
        assert proposal.confirmed_at is not None

        # 3. Undo (Chemist taps Undo button)
        undo_row = undo_last_change(
            test_session,
            history_id=history.id,
            undone_by_user_id=sample_user.id,
            pharmacy_id=sample_pharmacy.id,
        )
        test_session.commit()

        # Check the DB writes as per example:
        # stock_history: id=202, ... change_type=UNDO, undo_of_history_id=201, ...
        # inventory (id=7): stock_status=IN_STOCK, quantity=50, last_confirmed_at=2026-01-15 10:00:00, ...
        assert undo_row.change_type == HistoryChangeType.undo
        assert undo_row.undo_of_history_id == history.id
        assert undo_row.previous_stock_status == StockStatus.out_of_stock
        assert undo_row.new_stock_status == StockStatus.in_stock
        assert undo_row.previous_quantity is None  # inventory.quantity was None after apply
        assert undo_row.new_quantity == 50
        assert undo_row.changed_by == sample_user.id
        # Check inventory restored
        test_session.refresh(inventory)
        assert inventory.stock_status == StockStatus.in_stock
        assert inventory.quantity == 50
        assert inventory.last_updated_by == sample_user.id
        assert inventory.last_update_source == UpdateSource.undo
        # Check that the original history row's undo_of_history_id remains NULL (append-only)
        test_session.refresh(history)
        assert history.undo_of_history_id is None
        # And that the undo row points to the original
        assert undo_row.undo_of_history_id == history.id