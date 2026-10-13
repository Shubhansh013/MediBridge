"""
Seed data for MediBridge database.

Creates demo pharmacies, medicines, users, inventory, and proposals.
All data is clearly fake for demo purposes. Idempotent - safe to run multiple times.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy.orm import Session

from .database import utc_now
from .models import (
    AliasType,
    HistoryChangeType,
    Inventory,
    Medicine,
    MedicineAlias,
    Pharmacy,
    PharmacyVerification,
    ProposalStatus,
    StaffRoleAtPharmacy,
    StockHistory,
    StockProposal,
    StockStatus,
    SubstitutionApproval,
    SubstitutionStatus,
    UpdateSource,
    User,
    UserPharmacy,
    UserRole,
)
from .normalize import normalize_composition
from .operations import (
    apply_confirmed_proposal,
    record_substitution_approval,
    undo_last_change,
)


def seed_database(session: Session) -> Dict[str, int]:
    """
    Seed the database with demo data. Idempotent.

    Args:
        session: Database session (does not commit; caller owns transaction)

    Returns:
        Dict with row counts per table
    """
    now = utc_now()

    # Check if already seeded
    existing_pharmacies = session.query(Pharmacy).count()
    if existing_pharmacies > 0:
        return {"note": "Already seeded, skipping"}

    # =============================================================================
    # 1. Pharmacies - 12 demo locations across 4 Delhi NCR localities
    # =============================================================================
    pharmacies = [
        # Saket (South Delhi)
        Pharmacy(
            name="MedPlus Saket",
            locality="Saket",
            address="Shop 12, Saket District Centre, New Delhi 110017",
            latitude=28.5244,
            longitude=77.2066,
            contact_phone="+91-11-26854200",
            telegram_chat_id=1000001,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="Apollo Pharmacy Saket",
            locality="Saket",
            address="A-4, Saket Market, New Delhi 110017",
            latitude=28.5222,
            longitude=77.2088,
            contact_phone="+91-11-26521100",
            telegram_chat_id=1000002,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="HealthKart Saket",
            locality="Saket",
            address="B-15, Saket PVR Complex, New Delhi 110017",
            latitude=28.5255,
            longitude=77.2050,
            contact_phone="+91-11-26854300",
            telegram_chat_id=1000003,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        # Connaught Place (Central Delhi)
        Pharmacy(
            name="Delhi Chemists CP",
            locality="Connaught Place",
            address="N-Block, Connaught Place, New Delhi 110001",
            latitude=28.6289,
            longitude=77.2065,
            contact_phone="+91-11-23417800",
            telegram_chat_id=1000004,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="Guardian Pharmacy CP",
            locality="Connaught Place",
            address="P-Block Inner Circle, Connaught Place, New Delhi 110001",
            latitude=28.6304,
            longitude=77.2177,
            contact_phone="+91-11-23417900",
            telegram_chat_id=1000005,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="Wellness Forever CP",
            locality="Connaught Place",
            address="E-Block Outer Circle, Connaught Place, New Delhi 110001",
            latitude=28.6295,
            longitude=77.2145,
            contact_phone="+91-11-23418000",
            telegram_chat_id=1000006,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        # Dwarka (West Delhi)
        Pharmacy(
            name="Cure Plus Dwarka",
            locality="Dwarka",
            address="Sector 12, Dwarka, New Delhi 110075",
            latitude=28.5921,
            longitude=77.0460,
            contact_phone="+91-11-25085100",
            telegram_chat_id=1000007,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="NetMeds Dwarka",
            locality="Dwarka",
            address="Plot 5, Sector 10 Market, Dwarka, New Delhi 110075",
            latitude=28.5899,
            longitude=77.0512,
            contact_phone="+91-11-25085200",
            telegram_chat_id=1000008,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="PharmEasy Dwarka",
            locality="Dwarka",
            address="Sector 11 Main Road, Dwarka, New Delhi 110075",
            latitude=28.5910,
            longitude=77.0488,
            contact_phone="+91-11-25085300",
            telegram_chat_id=1000009,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        # Rohini (North Delhi)
        Pharmacy(
            name="LifeCare Rohini",
            locality="Rohini",
            address="Sector 7, Rohini, New Delhi 110085",
            latitude=28.7326,
            longitude=77.1135,
            contact_phone="+91-11-27895400",
            telegram_chat_id=1000010,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="Sunrise Pharmacy Rohini",
            locality="Rohini",
            address="Plot 12, Sector 3 Market, Rohini, New Delhi 110085",
            latitude=28.7288,
            longitude=77.1188,
            contact_phone="+91-11-27895500",
            telegram_chat_id=1000011,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
        Pharmacy(
            name="MedEasy Rohini",
            locality="Rohini",
            address="Sector 11, Rohini, New Delhi 110085",
            latitude=28.7355,
            longitude=77.1077,
            contact_phone="+91-11-27895600",
            telegram_chat_id=1000012,
            verification_state=PharmacyVerification.verified,
            is_active=True,
        ),
    ]
    session.add_all(pharmacies)
    session.flush()

    # =============================================================================
    # 2. Users - all roles
    # =============================================================================
    users = [
        # Patients
        User(
            telegram_user_id=100001,
            display_name="Priya Sharma",
            role=UserRole.patient,
            is_active=True,
        ),
        User(
            telegram_user_id=100002,
            display_name="Rahul Kumar",
            role=UserRole.patient,
            is_active=True,
        ),
        # Chemists (one linked to TWO pharmacies)
        User(
            telegram_user_id=200001,
            display_name="Amit Verma",
            role=UserRole.chemist,
            is_active=True,
        ),
        User(
            telegram_user_id=200002,
            display_name="Sneha Gupta",
            role=UserRole.chemist,
            is_active=True,
        ),
        User(
            telegram_user_id=200003,
            display_name="Rajesh Singh",
            role=UserRole.chemist,
            is_active=True,
        ),
        User(
            telegram_user_id=200004,
            display_name="Deepa Nair",
            role=UserRole.chemist,
            is_active=True,
        ),
        # Pharmacists
        User(
            telegram_user_id=300001,
            display_name="Dr. Meena Iyer",
            role=UserRole.pharmacist,
            is_active=True,
        ),
        User(
            telegram_user_id=300002,
            display_name="Dr. Suresh Reddy",
            role=UserRole.pharmacist,
            is_active=True,
        ),
        # Admin
        User(
            telegram_user_id=400001,
            display_name="Admin User",
            role=UserRole.admin,
            is_active=True,
        ),
    ]
    session.add_all(users)
    session.flush()

    # =============================================================================
    # 3. User-Pharmacy Links
    # =============================================================================
    # Amit Verma (chemist) works at BOTH MedPlus Saket AND Apollo Saket
    user_pharmacy_links = [
        UserPharmacy(user_id=users[2].id, pharmacy_id=pharmacies[0].id, role_at_pharmacy=StaffRoleAtPharmacy.chemist),  # Amit -> MedPlus Saket
        UserPharmacy(user_id=users[2].id, pharmacy_id=pharmacies[1].id, role_at_pharmacy=StaffRoleAtPharmacy.chemist),  # Amit -> Apollo Saket
        UserPharmacy(user_id=users[3].id, pharmacy_id=pharmacies[2].id, role_at_pharmacy=StaffRoleAtPharmacy.chemist),  # Sneha -> HealthKart Saket
        UserPharmacy(user_id=users[4].id, pharmacy_id=pharmacies[3].id, role_at_pharmacy=StaffRoleAtPharmacy.chemist),  # Rajesh -> Delhi Chemists CP
        UserPharmacy(user_id=users[5].id, pharmacy_id=pharmacies[6].id, role_at_pharmacy=StaffRoleAtPharmacy.chemist),  # Deepa -> Cure Plus Dwarka
        UserPharmacy(user_id=users[6].id, pharmacy_id=pharmacies[0].id, role_at_pharmacy=StaffRoleAtPharmacy.pharmacist),  # Dr. Meena -> MedPlus Saket
        UserPharmacy(user_id=users[7].id, pharmacy_id=pharmacies[3].id, role_at_pharmacy=StaffRoleAtPharmacy.pharmacist),  # Dr. Suresh -> Delhi Chemists CP
    ]
    session.add_all(user_pharmacy_links)
    session.flush()

    # =============================================================================
    # 4. Medicines - ~25 brands, ~12 compositions
    # =============================================================================
    medicines = [
        # Paracetamol 650mg tablets (3 equivalent brands)
        Medicine(
            brand_name="Dolo 650",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="650mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Micro Labs",
            is_active=True,
        ),
        Medicine(
            brand_name="Calpol 650",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="650mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="GlaxoSmithKline",
            is_active=True,
        ),
        Medicine(
            brand_name="Crocin 650",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="650mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="GlaxoSmithKline",
            is_active=True,
        ),
        # Paracetamol syrup
        Medicine(
            brand_name="Calpol Syrup",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="250mg/5ml",
            form="syrup",
            is_schedule_h=False,
            manufacturer="GlaxoSmithKline",
            is_active=True,
        ),
        # Combination: Paracetamol + Ibuprofen
        Medicine(
            brand_name="Combiflam",
            generic_name="Paracetamol + Ibuprofen",
            composition_key=normalize_composition("Paracetamol + Ibuprofen"),
            strength="325mg+400mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Sanofi",
            is_active=True,
        ),
        # Antibiotics (Schedule H)
        Medicine(
            brand_name="Augmentin 625",
            generic_name="Amoxicillin + Clavulanic Acid",
            composition_key=normalize_composition("Amoxicillin + Clavulanic Acid"),
            strength="500mg+125mg",
            form="tablet",
            is_schedule_h=True,
            manufacturer="GlaxoSmithKline",
            is_active=True,
        ),
        Medicine(
            brand_name="Azithral 500",
            generic_name="Azithromycin",
            composition_key=normalize_composition("Azithromycin"),
            strength="500mg",
            form="tablet",
            is_schedule_h=True,
            manufacturer="Alembic",
            is_active=True,
        ),
        # Common OTC medicines
        Medicine(
            brand_name="Cetrizine 10",
            generic_name="Cetirizine",
            composition_key=normalize_composition("Cetirizine"),
            strength="10mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Cipla",
            is_active=True,
        ),
        Medicine(
            brand_name="Allegra 120",
            generic_name="Fexofenadine",
            composition_key=normalize_composition("Fexofenadine"),
            strength="120mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Sanofi",
            is_active=True,
        ),
        Medicine(
            brand_name="Pan 40",
            generic_name="Pantoprazole",
            composition_key=normalize_composition("Pantoprazole"),
            strength="40mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Alkem",
            is_active=True,
        ),
        Medicine(
            brand_name="Omez 20",
            generic_name="Omeprazole",
            composition_key=normalize_composition("Omeprazole"),
            strength="20mg",
            form="capsule",
            is_schedule_h=False,
            manufacturer="Dr. Reddy's",
            is_active=True,
        ),
        Medicine(
            brand_name="Disprin",
            generic_name="Aspirin",
            composition_key=normalize_composition("Aspirin"),
            strength="325mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Reckitt Benckiser",
            is_active=True,
        ),
        Medicine(
            brand_name="Brufen 400",
            generic_name="Ibuprofen",
            composition_key=normalize_composition("Ibuprofen"),
            strength="400mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Abbott",
            is_active=True,
        ),
        Medicine(
            brand_name="Sinarest",
            generic_name="Paracetamol + Chlorpheniramine + Phenylephrine",
            composition_key=normalize_composition("Paracetamol + Chlorpheniramine + Phenylephrine"),
            strength="500mg+2mg+5mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Centaur",
            is_active=True,
        ),
        Medicine(
            brand_name="Crocin Advance",
            generic_name="Paracetamol",
            composition_key=normalize_composition("Paracetamol"),
            strength="500mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="GlaxoSmithKline",
            is_active=True,
        ),
        Medicine(
            brand_name="Vicks Vaporub",
            generic_name="Camphor + Menthol + Eucalyptus",
            composition_key=normalize_composition("Camphor + Menthol + Eucalyptus"),
            strength="various",
            form="ointment",
            is_schedule_h=False,
            manufacturer="Procter & Gamble",
            is_active=True,
        ),
        Medicine(
            brand_name="D-Cold Total",
            generic_name="Paracetamol + Phenylephrine + Cetirizine + Caffeine",
            composition_key=normalize_composition("Paracetamol + Phenylephrine + Cetirizine + Caffeine"),
            strength="325mg+5mg+5mg+25mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Paras",
            is_active=True,
        ),
        Medicine(
            brand_name="Volini Gel",
            generic_name="Diclofenac",
            composition_key=normalize_composition("Diclofenac"),
            strength="1%",
            form="gel",
            is_schedule_h=False,
            manufacturer="Ranbaxy",
            is_active=True,
        ),
        Medicine(
            brand_name="Moov Cream",
            generic_name="Diclofenac + Menthol + Linseed Oil",
            composition_key=normalize_composition("Diclofenac + Menthol + Linseed Oil"),
            strength="various",
            form="cream",
            is_schedule_h=False,
            manufacturer="Reckitt Benckiser",
            is_active=True,
        ),
        Medicine(
            brand_name="Becosules Capsules",
            generic_name="Vitamin B Complex",
            composition_key=normalize_composition("Vitamin B Complex"),
            strength="various",
            form="capsule",
            is_schedule_h=False,
            manufacturer="Pfizer",
            is_active=True,
        ),
        Medicine(
            brand_name="Shelcal 500",
            generic_name="Calcium Carbonate + Vitamin D3",
            composition_key=normalize_composition("Calcium Carbonate + Vitamin D3"),
            strength="500mg+250IU",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Torrent",
            is_active=True,
        ),
        Medicine(
            brand_name="Zincovit Tablets",
            generic_name="Multivitamin + Zinc",
            composition_key=normalize_composition("Multivitamin + Zinc"),
            strength="various",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Apex",
            is_active=True,
        ),
        Medicine(
            brand_name="Electral Powder",
            generic_name="Oral Rehydration Salts",
            composition_key=normalize_composition("Oral Rehydration Salts"),
            strength="21.8g",
            form="powder",
            is_schedule_h=False,
            manufacturer="FDC",
            is_active=True,
        ),
        Medicine(
            brand_name="Imodium",
            generic_name="Loperamide",
            composition_key=normalize_composition("Loperamide"),
            strength="2mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Johnson & Johnson",
            is_active=True,
        ),
        Medicine(
            brand_name="Norflox TZ",
            generic_name="Norfloxacin + Tinidazole",
            composition_key=normalize_composition("Norfloxacin + Tinidazole"),
            strength="400mg+600mg",
            form="tablet",
            is_schedule_h=True,
            manufacturer="Cipla",
            is_active=True,
        ),
    ]
    session.add_all(medicines)
    session.flush()

    # =============================================================================
    # 5. Medicine Aliases
    # =============================================================================
    aliases = [
        # Dolo 650
        MedicineAlias(medicine_id=medicines[0].id, alias="dolo650", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[0].id, alias="dolo", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[0].id, alias="dolo six fifty", alias_type=AliasType.hinglish),
        # Calpol
        MedicineAlias(medicine_id=medicines[1].id, alias="calpol", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[1].id, alias="kalpol", alias_type=AliasType.misspelling),
        # Crocin
        MedicineAlias(medicine_id=medicines[2].id, alias="crocin", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[2].id, alias="krocin", alias_type=AliasType.misspelling),
        # Combiflam
        MedicineAlias(medicine_id=medicines[4].id, alias="combiflam", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[4].id, alias="combiflame", alias_type=AliasType.misspelling),
        # Augmentin
        MedicineAlias(medicine_id=medicines[5].id, alias="augmentin", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[5].id, alias="augmentin625", alias_type=AliasType.short),
        # Cetrizine
        MedicineAlias(medicine_id=medicines[7].id, alias="cetrizine", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[7].id, alias="cetirizine", alias_type=AliasType.misspelling),
        # Pan 40
        MedicineAlias(medicine_id=medicines[9].id, alias="pan40", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[9].id, alias="pan", alias_type=AliasType.short),
        # Disprin
        MedicineAlias(medicine_id=medicines[11].id, alias="disprin", alias_type=AliasType.short),
        MedicineAlias(medicine_id=medicines[11].id, alias="dispirin", alias_type=AliasType.misspelling),
    ]
    session.add_all(aliases)
    session.flush()

    # =============================================================================
    # 6. Inventory - mixed statuses and ages
    # =============================================================================
    # Dolo 650 OUT_OF_STOCK at MedPlus Saket, but Calpol 650 IN_STOCK nearby at Apollo
    inventory_rows = [
        # MedPlus Saket (pharmacy 0)
        Inventory(
            pharmacy_id=pharmacies[0].id,
            medicine_id=medicines[0].id,  # Dolo 650 OUT_OF_STOCK
            stock_status=StockStatus.out_of_stock,
            quantity=0,
            last_confirmed_at=now - timedelta(hours=2),  # Fresh
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[0].id,
            medicine_id=medicines[4].id,  # Combiflam IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=100,
            last_confirmed_at=now - timedelta(hours=1),  # Fresh
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[0].id,
            medicine_id=medicines[5].id,  # Augmentin LOW_STOCK (Schedule H)
            stock_status=StockStatus.low_stock,
            quantity=5,
            last_confirmed_at=now - timedelta(hours=30),  # Stale (>24h)
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        # Apollo Saket (pharmacy 1) - nearby, has Calpol 650
        Inventory(
            pharmacy_id=pharmacies[1].id,
            medicine_id=medicines[1].id,  # Calpol 650 IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=50,
            last_confirmed_at=now - timedelta(hours=3),  # Fresh
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[1].id,
            medicine_id=medicines[2].id,  # Crocin 650 IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=30,
            last_confirmed_at=now - timedelta(hours=4),  # Fresh
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[1].id,
            medicine_id=medicines[7].id,  # Cetrizine IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=80,
            last_confirmed_at=now - timedelta(hours=26),  # Stale
            last_updated_by=users[2].id,
            last_update_source=UpdateSource.seed,
        ),
        # HealthKart Saket (pharmacy 2)
        Inventory(
            pharmacy_id=pharmacies[2].id,
            medicine_id=medicines[0].id,  # Dolo 650 IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=40,
            last_confirmed_at=now - timedelta(hours=5),  # Fresh
            last_updated_by=users[3].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[2].id,
            medicine_id=medicines[9].id,  # Pan 40 IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=60,
            last_confirmed_at=now - timedelta(hours=10),  # Fresh
            last_updated_by=users[3].id,
            last_update_source=UpdateSource.seed,
        ),
        # Delhi Chemists CP (pharmacy 3)
        Inventory(
            pharmacy_id=pharmacies[3].id,
            medicine_id=medicines[4].id,  # Combiflam IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=70,
            last_confirmed_at=now - timedelta(hours=6),  # Fresh
            last_updated_by=users[4].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[3].id,
            medicine_id=medicines[11].id,  # Disprin IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=90,
            last_confirmed_at=now - timedelta(hours=48),  # Stale
            last_updated_by=users[4].id,
            last_update_source=UpdateSource.seed,
        ),
        # Cure Plus Dwarka (pharmacy 6)
        Inventory(
            pharmacy_id=pharmacies[6].id,
            medicine_id=medicines[0].id,  # Dolo 650 LOW_STOCK
            stock_status=StockStatus.low_stock,
            quantity=8,
            last_confirmed_at=now - timedelta(hours=7),  # Fresh
            last_updated_by=users[5].id,
            last_update_source=UpdateSource.seed,
        ),
        Inventory(
            pharmacy_id=pharmacies[6].id,
            medicine_id=medicines[3].id,  # Calpol Syrup IN_STOCK
            stock_status=StockStatus.in_stock,
            quantity=25,
            last_confirmed_at=now - timedelta(hours=12),  # Fresh
            last_updated_by=users[5].id,
            last_update_source=UpdateSource.seed,
        ),
    ]
    session.add_all(inventory_rows)
    session.flush()

    # =============================================================================
    # 7. Stock Proposals - pending, flagged, expired, with source_message_ref
    # =============================================================================
    proposals = [
        # Fresh pending proposal
        StockProposal(
            pharmacy_id=pharmacies[0].id,
            chemist_user_id=users[2].id,
            raw_message="Dolo 650 stock khatam ho gaya",
            brand_name="Dolo 650",
            parsed_salt="Paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=0,
            parsed_intent=StockStatus.out_of_stock,
            confidence=0.95,
            status=ProposalStatus.pending,
            flagged_for_review=False,
            expires_at=now + timedelta(hours=12),
            source_message_ref="msg_tg_12345",
        ),
        # Flagged pending (low confidence)
        StockProposal(
            pharmacy_id=pharmacies[1].id,
            chemist_user_id=users[2].id,
            raw_message="augmentin low ho gaya maybe 5-6 strips",
            brand_name="Augmentin 625",
            parsed_salt="Amoxicillin + Clavulanic Acid",
            parsed_strength="500mg+125mg",
            parsed_form="tablet",
            parsed_quantity=5,
            parsed_intent=StockStatus.low_stock,
            confidence=0.72,
            status=ProposalStatus.pending,
            flagged_for_review=True,  # Low confidence
            expires_at=now + timedelta(hours=18),
            source_message_ref="msg_tg_12346",
        ),
        # Expired pending proposal
        StockProposal(
            pharmacy_id=pharmacies[2].id,
            chemist_user_id=users[3].id,
            raw_message="combiflam out of stock",
            brand_name="Combiflam",
            parsed_salt="Paracetamol + Ibuprofen",
            parsed_strength="325mg+400mg",
            parsed_form="tablet",
            parsed_quantity=0,
            parsed_intent=StockStatus.out_of_stock,
            confidence=0.88,
            status=ProposalStatus.pending,
            flagged_for_review=False,
            expires_at=now - timedelta(hours=2),  # Already expired
            source_message_ref="msg_tg_12347",
        ),
        # Two proposals with same source_message_ref
        StockProposal(
            pharmacy_id=pharmacies[3].id,
            chemist_user_id=users[4].id,
            raw_message="cetrizine and pan 40 dono available",
            brand_name="Cetrizine 10",
            parsed_salt="Cetirizine",
            parsed_strength="10mg",
            parsed_form="tablet",
            parsed_quantity=None,
            parsed_intent=StockStatus.in_stock,
            confidence=0.91,
            status=ProposalStatus.pending,
            flagged_for_review=False,
            expires_at=now + timedelta(hours=20),
            source_message_ref="msg_tg_12348_batch",  # Same ref
        ),
        StockProposal(
            pharmacy_id=pharmacies[3].id,
            chemist_user_id=users[4].id,
            raw_message="cetrizine and pan 40 dono available",
            brand_name="Pan 40",
            parsed_salt="Pantoprazole",
            parsed_strength="40mg",
            parsed_form="tablet",
            parsed_quantity=None,
            parsed_intent=StockStatus.in_stock,
            confidence=0.90,
            status=ProposalStatus.pending,
            flagged_for_review=False,
            expires_at=now + timedelta(hours=20),
            source_message_ref="msg_tg_12348_batch",  # Same ref
        ),
    ]
    session.add_all(proposals)
    session.flush()

    # =============================================================================
    # 8. Applied proposal with history (use real operations)
    # =============================================================================
    # Confirm and apply one proposal to create history
    proposals[0].status = ProposalStatus.confirmed
    session.flush()

    try:
        apply_confirmed_proposal(
            session,
            proposal_id=proposals[0].id,
            confirmed_by_user_id=users[2].id,
            pharmacy_id=pharmacies[0].id,
            current_time=now,
        )
        session.flush()
    except Exception:
        # Inventory already out_of_stock, apply may fail - that's OK for demo
        pass

    # =============================================================================
    # 9. Undo operation (use real operations)
    # =============================================================================
    # Get the latest history entry for the Dolo 650 inventory at MedPlus Saket
    # and undo it to demonstrate undo functionality
    history_entries = session.query(StockHistory).filter(
        StockHistory.inventory_id == inventory_rows[0].id
    ).order_by(StockHistory.created_at.desc()).all()

    if history_entries:
        try:
            undo_last_change(
                session,
                history_id=history_entries[0].id,
                undone_by_user_id=users[2].id,
                pharmacy_id=pharmacies[0].id,
            )
            session.flush()
        except Exception:
            # Undo may fail for various reasons - that's OK for demo
            pass

    # =============================================================================
    # 10. Substitution Approval (use real operations)
    # =============================================================================
    # Pharmacist approves Calpol 650 as substitute for Dolo 650 at MedPlus Saket
    try:
        record_substitution_approval(
            session,
            pharmacy_id=pharmacies[0].id,
            requested_medicine_id=medicines[0].id,  # Dolo 650
            substitute_medicine_id=medicines[1].id,  # Calpol 650
            approving_pharmacist_id=users[6].id,  # Dr. Meena
            status=SubstitutionStatus.approved,
            request_reference="Patient Priya Sharma request",
        )
        session.flush()
    except Exception:
        # May fail if composition doesn't match - that's OK
        pass

    # =============================================================================
    # Count rows
    # =============================================================================
    counts = {
        "pharmacies": session.query(Pharmacy).count(),
        "users": session.query(User).count(),
        "user_pharmacies": session.query(UserPharmacy).count(),
        "medicines": session.query(Medicine).count(),
        "medicine_aliases": session.query(MedicineAlias).count(),
        "inventory": session.query(Inventory).count(),
        "stock_proposals": session.query(StockProposal).count(),
        "stock_history": session.query(StockHistory).count(),
        "substitution_approvals": session.query(SubstitutionApproval).count(),
    }

    return counts
