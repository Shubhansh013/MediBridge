/**
 * MediBridge Mobile Application Controller
 * Connects directly to FastAPI Gateway endpoints (/api/*)
 * Enforces human-in-the-loop stock verification and strict scoping
 */

const API_BASE = '/api';

// State management
const state = {
  currentRole: 'patient', // 'patient' | 'chemist'
  chemistUserId: 3, // Default demo chemist user (Sharma Ji / MedPlus Saket)
  activePharmacyId: null,
  activePharmacyName: '',
  userPharmacies: [],
  userCoords: { lat: 28.5244, lon: 77.2066 }, // Saket, New Delhi default
  searchDebounceTimer: null,
  rawInventory: [],
  lastSubstitutedSearch: null,
  currentSelectedSubstitute: null,
  isGpsLocation: false
};

// ================= INITIALIZATION & NAVIGATION =================

document.addEventListener('DOMContentLoaded', async () => {
  setupSearchInput();
  await loadChemistPharmacies();
  // Start with Patient mode: Search tab, empty input, All filter selected
  await loadNearbyPharmacies();
});

function switchRole(role) {
  state.currentRole = role;
  const isChemist = role === 'chemist';

  // Toggle button styles
  document.getElementById('btn-role-patient').classList.toggle('active', !isChemist);
  document.getElementById('btn-role-chemist').classList.toggle('active', isChemist);

  // Toggle Context Bar
  document.getElementById('chemist-context-bar').style.display = isChemist ? 'flex' : 'none';

  // Toggle Bottom Navigation items
  document.querySelectorAll('.patient-nav').forEach(el => el.style.display = isChemist ? 'none' : 'flex');
  document.querySelectorAll('.chemist-nav').forEach(el => el.style.display = isChemist ? 'flex' : 'none');

  if (isChemist) {
    navigateTo('view-chemist-update');
    updateProposalBadgeCount();
  } else {
    navigateTo('view-search');
  }
}

function navigateTo(viewId) {
  // Hide all views
  document.querySelectorAll('.view-section').forEach(view => view.classList.remove('active'));

  // Show target view
  const target = document.getElementById(viewId);
  if (target) target.classList.add('active');

  // Update active bottom nav
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
  const navMap = {
    'view-search': 'nav-btn-search',
    'view-pharmacies': 'nav-btn-pharmacies',
    'view-info': 'nav-btn-info',
    'view-substitute-availability': 'nav-btn-search',
    'view-chemist-update': 'nav-btn-update',
    'view-chemist-proposals': 'nav-btn-proposals',
    'view-chemist-inventory': 'nav-btn-inventory',
    'view-chemist-history': 'nav-btn-history'
  };

  const activeNavId = navMap[viewId];
  if (activeNavId && document.getElementById(activeNavId)) {
    document.getElementById(activeNavId).classList.add('active');
  }

  // Trigger lazy data loads per view
  if (viewId === 'view-chemist-proposals') {
    loadPendingProposals();
  } else if (viewId === 'view-chemist-inventory') {
    loadPharmacyInventory();
  } else if (viewId === 'view-chemist-history') {
    loadPharmacyHistory();
  }
}

// ================= PATIENT MODULE: MEDICINE SEARCH & DISCOVERY =================

function setupSearchInput() {
  const input = document.getElementById('medicine-search-input');
  const clearBtn = document.getElementById('btn-clear-search');

  input.addEventListener('input', (e) => {
    const val = e.target.value.trim();
    clearBtn.style.display = val ? 'block' : 'none';

    clearTimeout(state.searchDebounceTimer);
    if (!val) {
      document.getElementById('search-results-list').innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🔍</div>
          <p>Type a medicine brand or generic composition above to search catalogue.</p>
        </div>`;
      return;
    }

    state.searchDebounceTimer = setTimeout(() => {
      executeMedicineSearch(val);
    }, 250);
  });
}

function clearMedicineSearch() {
  const input = document.getElementById('medicine-search-input');
  input.value = '';
  document.getElementById('btn-clear-search').style.display = 'none';
  document.getElementById('search-results-list').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">🔍</div>
      <p>Type a medicine brand or generic composition above to search catalogue.</p>
    </div>`;
}

function quickFilter(query) {
  const input = document.getElementById('medicine-search-input');
  const container = document.getElementById('search-results-list');
  input.value = query;
  document.getElementById('btn-clear-search').style.display = query ? 'block' : 'none';

  // Update active pill
  document.querySelectorAll('.chips-scroll .chip').forEach(c => {
    c.classList.toggle('active', c.textContent.trim().toLowerCase().includes(query.toLowerCase()) && query !== '');
  });

  if (query) {
    executeMedicineSearch(query);
  } else {
    // Load all medicines from catalogue (All button)
    fetch(`${API_BASE}/medicines/`)
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load catalogue');
        return res.json();
      })
      .then((medicines) => {
        if (!medicines || medicines.length === 0) {
          container.innerHTML = `
            <div class="empty-state">
              <div class="empty-icon">💊</div>
              <p>No medicines in catalogue.</p>
            </div>`;
          return;
        }
        container.innerHTML = medicines.map(med => {
          const scheduleHBadge = med.is_schedule_h
            ? `<span class="badge badge-schedule-h" title="Prescription Required">Schedule H (Rx)</span>`
            : '';
          return `
            <div class="card">
              <div class="card-header">
                <div>
                  <div class="card-title">${escapeHtml(med.brand_name)}</div>
                  <div class="card-meta">${escapeHtml(med.generic_salt)} • ${escapeHtml(med.strength)}</div>
                </div>
                ${scheduleHBadge}
              </div>
              <div style="font-size: 0.75rem; color: var(--slate-500); margin-bottom: 10px;">
                Dosage Form: <strong>${escapeHtml(med.form || 'Tablet')}</strong>
              </div>
              <div style="display: flex; gap: 8px;">
                <button class="btn btn-secondary" onclick="viewSubstitutes(${med.id}, '${escapeHtml(med.brand_name)}', '${escapeHtml(med.generic_salt)}')">
                  Find Substitutes
                </button>
                <button class="btn btn-primary" onclick="searchStockForMedicine(${med.id}, '${escapeHtml(med.brand_name)}')">
                  Check Stores
                </button>
              </div>
            </div>`;
        }).join('');
      })
      .catch((err) => {
        container.innerHTML = `
          <div class="empty-state">
            <p style="color: var(--danger);">Failed to load catalogue: ${escapeHtml(err.message)}</p>
          </div>`;
      });
  }
}

async function executeMedicineSearch(query) {
  const container = document.getElementById('search-results-list');
  container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Searching catalogue...</p></div>`;

  try {
    const res = await fetch(`${API_BASE}/medicines/search?q=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error('Search failed');
    const data = await res.json();

    if (!data || data.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">💊</div>
          <p>No medicines matched "<strong>${escapeHtml(query)}</strong>". Try searching generic composition (e.g., Paracetamol).</p>
        </div>`;
      return;
    }

    container.innerHTML = data.map(med => {
      const scheduleHBadge = med.is_schedule_h
        ? `<span class="badge badge-schedule-h" title="Prescription Required">Schedule H (Rx)</span>`
        : '';

      return `
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">${escapeHtml(med.brand_name)}</div>
              <div class="card-meta">${escapeHtml(med.generic_salt)} • ${escapeHtml(med.strength)}</div>
            </div>
            ${scheduleHBadge}
          </div>
          <div style="font-size: 0.75rem; color: var(--slate-500); margin-bottom: 10px;">
            Dosage Form: <strong>${escapeHtml(med.form || 'Tablet')}</strong>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="btn btn-secondary" onclick="viewSubstitutes(${med.id}, '${escapeHtml(med.brand_name)}', '${escapeHtml(med.generic_salt)}')">
              Find Substitutes
            </button>
            <button class="btn btn-primary" onclick="searchStockForMedicine(${med.id}, '${escapeHtml(med.brand_name)}')">
              Check Stores
            </button>
          </div>
        </div>`;
    }).join('');

  } catch (err) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="color: var(--danger);">Failed to load search results: ${escapeHtml(err.message)}</p>
      </div>`;
  }
}

// ================= PATIENT MODULE: NEARBY PHARMACIES =================

function useCurrentLocation() {
  if (navigator.geolocation) {
    showToast('Requesting GPS location...', 'warning');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userCoords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        showToast('Location updated', 'success');
        loadNearbyPharmacies();
      },
      (err) => {
        showToast('Could not get GPS. Using New Delhi Saket area.', 'warning');
        loadNearbyPharmacies();
      }
    );
  } else {
    showToast('Geolocation not supported by browser', 'error');
  }
}

async function loadNearbyPharmacies() {
  const container = document.getElementById('nearby-pharmacies-list');
  const radius = document.getElementById('radius-select').value;
  container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Finding nearby verified pharmacies...</p></div>`;

  try {
    const res = await fetch(`${API_BASE}/pharmacies/nearby?lat=${state.userCoords.lat}&lon=${state.userCoords.lon}&radius_km=${radius}`);
    if (!res.ok) throw new Error('Failed to fetch nearby pharmacies');
    const pharmacies = await res.json();

    if (!pharmacies || pharmacies.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🏪</div>
          <p>No verified pharmacies found within ${radius} km of your location.</p>
        </div>`;
      return;
    }

    container.innerHTML = pharmacies.map(ph => {
      const dist = ph.distance_km != null ? (state.isGpsLocation ? `${ph.distance_km.toFixed(1)} km from you` : `~${ph.distance_km.toFixed(1)} km (Saket ref)`) : '';
      const verifiedBadge = ph.is_verified
        ? `<span class="badge badge-in-stock">Verified</span>`
        : `<span class="badge badge-low-stock">Unverified</span>`;

      return `
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">${escapeHtml(ph.name)}</div>
              <div class="card-meta">${escapeHtml(ph.address || '')}</div>
            </div>
            ${verifiedBadge}
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
            <span style="font-size: 0.75rem; color: var(--slate-600); font-weight: 600;">📍 ${dist}</span>
            <button class="btn btn-secondary" onclick="viewPharmacyPublicInventory(${ph.id}, '${escapeHtml(ph.name)}')">
              View Available Stock
            </button>
          </div>
        </div>`;
    }).join('');

  } catch (err) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="color: var(--danger);">Failed to load pharmacies: ${escapeHtml(err.message)}</p>
      </div>`;
  }
}

async function viewPharmacyPublicInventory(pharmacyId, pharmacyName) {
  // Switch to chemist inventory view in read-only inspection mode
  state.activePharmacyId = pharmacyId;
  state.activePharmacyName = pharmacyName;
  const selector = document.getElementById('pharmacy-selector');
  if (selector) selector.value = pharmacyId;
  navigateTo('view-chemist-inventory');
}

// ================= PATIENT MODULE: SUBSTITUTES MODAL & DEDICATED AVAILABILITY =================

async function viewSubstitutes(medicineId, brandName, genericSalt) {
  state.lastSubstitutedSearch = { medicineId, brandName, genericSalt };
  const modal = document.getElementById('substitutes-modal');
  const body = document.getElementById('substitutes-modal-body');
  document.getElementById('modal-sub-brand').textContent = `${brandName} Substitutes`;
  document.getElementById('modal-sub-salt').textContent = `Matching: ${genericSalt}`;

  body.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Finding equivalent formulations...</p></div>`;
  modal.style.display = 'flex';

  try {
    const res = await fetch(`${API_BASE}/medicines/${medicineId}/substitutes`);
    if (!res.ok) throw new Error('Could not fetch substitutes');
    const data = await res.json();

    if (!data || data.length === 0) {
      body.innerHTML = `
        <div class="empty-state">
          <p>No exact salt/strength substitutes found for this brand in catalogue.</p>
        </div>`;
      return;
    }

    body.innerHTML = `
      <div style="font-size: 0.8rem; color: var(--slate-500); margin-bottom: 12px;">
        Found ${data.length} therapeutic alternative(s) with matching chemical formulation:
      </div>
      ${data.map(sub => {
        const saltName = sub.generic_name || sub.generic_salt || genericSalt || '';
        const strengthStr = sub.strength || '';
        const formStr = sub.form || 'Tablet';
        const isRx = Boolean(sub.is_schedule_h);
        return `
        <div class="card substitute-card" onclick="selectSubstitute(${sub.id}, '${escapeHtml(sub.brand_name)}', '${escapeHtml(saltName)}', '${escapeHtml(strengthStr)}', '${escapeHtml(formStr)}', ${isRx})" style="margin-bottom: 10px; cursor: pointer;">
          <div class="card-header">
            <div>
              <div class="card-title">${escapeHtml(sub.brand_name)}</div>
              <div class="card-meta">${escapeHtml(saltName)} • ${escapeHtml(strengthStr)}</div>
            </div>
            ${isRx ? '<span class="badge badge-schedule-h">Rx Required</span>' : '<span class="badge badge-in-stock">OTC</span>'}
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px;">
            <span style="font-size: 0.75rem; color: var(--slate-500);">Form: ${escapeHtml(formStr)}</span>
            <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); selectSubstitute(${sub.id}, '${escapeHtml(sub.brand_name)}', '${escapeHtml(saltName)}', '${escapeHtml(strengthStr)}', '${escapeHtml(formStr)}', ${isRx})">
              Check Stock &amp; Stores →
            </button>
          </div>
        </div>`;
      }).join('')}`;

  } catch (err) {
    body.innerHTML = `<div class="empty-state"><p style="color: var(--danger);">${escapeHtml(err.message)}</p></div>`;
  }
}

function closeSubstitutesModal() {
  document.getElementById('substitutes-modal').style.display = 'none';
}

function selectSubstitute(medicineId, brandName, genericSalt, strength, form, isScheduleH) {
  closeSubstitutesModal();
  state.currentSelectedSubstitute = { medicineId, brandName, genericSalt, strength, form, isScheduleH };

  const brandEl = document.getElementById('sub-page-brand');
  const saltEl = document.getElementById('sub-page-salt');
  const metaEl = document.getElementById('sub-page-meta');
  const badgeEl = document.getElementById('sub-page-badge');

  if (brandEl) brandEl.textContent = brandName;
  if (saltEl) saltEl.textContent = genericSalt ? `Active Salt: ${genericSalt}` : '';
  if (metaEl) metaEl.textContent = `Formulation: ${form || 'Tablet'} • Strength: ${strength || 'Standard'}`;
  if (badgeEl) {
    badgeEl.innerHTML = isScheduleH
      ? '<span class="badge badge-schedule-h">Schedule H (Rx Required)</span>'
      : '<span class="badge badge-in-stock">OTC / General</span>';
  }

  navigateTo('view-substitute-availability');
  loadSubstitutePharmacies(medicineId);
}

function backFromSubstituteAvailability() {
  navigateTo('view-search');
  if (state.lastSubstitutedSearch) {
    const { medicineId, brandName, genericSalt } = state.lastSubstitutedSearch;
    viewSubstitutes(medicineId, brandName, genericSalt);
  }
}

function refreshSubstituteAvailability() {
  if (state.currentSelectedSubstitute) {
    loadSubstitutePharmacies(state.currentSelectedSubstitute.medicineId);
  }
}

async function loadSubstitutePharmacies(medicineId) {
  const container = document.getElementById('substitute-pharmacies-list');
  if (!container) return;

  const currentMed = state.currentSelectedSubstitute;
  const medName = currentMed ? currentMed.brandName : 'medicine';

  container.innerHTML = `
    <div class="empty-state">
      <div class="spinner"></div>
      <p style="margin-top: 10px;">Checking verified pharmacies stocking ${escapeHtml(medName)}...</p>
    </div>`;

  try {
    const url = `${API_BASE}/medicines/${medicineId}/pharmacies?lat=${state.userCoords.lat}&lon=${state.userCoords.lon}`;
    const res = await fetch(url);
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Server returned HTTP ${res.status}`);
    }
    const pharmacies = await res.json();

    if (!pharmacies || pharmacies.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🏪</div>
          <div style="font-weight: 700; color: var(--slate-800); margin-bottom: 6px;">No Confirmed Stock Found</div>
          <p style="font-size: 0.82rem; color: var(--slate-600); margin-bottom: 14px; max-width: 320px; margin-left: auto; margin-right: auto;">
            No confirmed availability found for this medicine. You can explore other alternatives or check again later.
          </p>
          <button class="btn btn-secondary btn-sm" onclick="backFromSubstituteAvailability()">
            ← Explore Other Alternatives
          </button>
        </div>`;
      return;
    }

    const locationNoticeHtml = state.isGpsLocation
      ? `<div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: var(--radius-sm); padding: 8px 12px; font-size: 0.76rem; color: #166534; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
           <span>📍 Distances calculated from your live GPS location</span>
           <span style="font-weight: 700;">Live GPS</span>
         </div>`
      : `<div style="background: #f8fafc; border: 1px solid var(--slate-200); border-radius: var(--radius-sm); padding: 8px 12px; font-size: 0.76rem; color: var(--slate-600); margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
           <span>📍 Distances use <strong>Saket, New Delhi</strong> default reference location.</span>
           <button class="btn btn-secondary btn-sm" style="padding: 2px 8px; font-size: 0.72rem;" onclick="requestGpsForSubstitutes()">Use My GPS</button>
         </div>`;

    container.innerHTML = locationNoticeHtml + pharmacies.map(ph => {
      let statusBadge = '';
      let freshnessHtml = '';
      let qtyStr = '';

      if (ph.is_stale) {
        // Explicit requirement: STALE — availability not recently verified.
        freshnessHtml = `<div class="stale-alert-badge" style="background: #fffbeb; color: #92400e; border: 1px solid #fde68a; border-radius: 6px; padding: 3px 8px; font-size: 0.72rem; font-weight: 700; display: inline-flex; align-items: center; gap: 4px; margin-top: 4px;">⏱️ STALE — availability not recently verified.</div>`;

        if (ph.stock_status === 'OUT_OF_STOCK') {
          statusBadge = `<span class="badge badge-out-of-stock">✕ Out of Stock</span>`;
        } else if (ph.stock_status === 'LOW_STOCK') {
          statusBadge = `<span class="badge" style="background: #fef3c7; color: #92400e; border: 1px solid #fde68a; font-weight: 600;">Unverified Low Stock (Stale)</span>`;
        } else {
          statusBadge = `<span class="badge" style="background: #fef3c7; color: #92400e; border: 1px solid #fde68a; font-weight: 600;">Unverified Stock (Stale)</span>`;
        }

        if (ph.quantity != null && ph.stock_status !== 'OUT_OF_STOCK') {
          qtyStr = `<div style="font-size: 0.74rem; color: #92400e; margin-top: 4px;">⚠️ Last recorded quantity: <strong>${ph.quantity} units</strong> (may be outdated)</div>`;
        }
      } else {
        if (ph.stock_status === 'IN_STOCK') {
          statusBadge = `<span class="badge badge-in-stock">● Confirmed In Stock</span>`;
        } else if (ph.stock_status === 'LOW_STOCK') {
          statusBadge = `<span class="badge badge-low-stock">▲ Confirmed Low Stock</span>`;
        } else {
          statusBadge = `<span class="badge badge-out-of-stock">✕ Out of Stock</span>`;
        }

        if (ph.last_confirmed_at) {
          try {
            const d = new Date(ph.last_confirmed_at);
            const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            freshnessHtml = `<span style="font-size: 0.72rem; color: var(--success); font-weight: 600;">✓ Confirmed today (${timeStr})</span>`;
          } catch (_) {
            freshnessHtml = `<span style="font-size: 0.72rem; color: var(--success); font-weight: 600;">✓ Confirmed recently</span>`;
          }
        }

        if (ph.quantity != null && ph.stock_status !== 'OUT_OF_STOCK') {
          qtyStr = `<div style="font-size: 0.74rem; color: var(--slate-600); font-weight: 600; margin-top: 4px;">Confirmed quantity: ${ph.quantity} units</div>`;
        }
      }

      let distStr = '';
      if (ph.distance_km != null) {
        distStr = state.isGpsLocation
          ? `📍 ${ph.distance_km} km from you`
          : `📍 ~${ph.distance_km} km (from Saket ref)`;
      }

      const lat = ph.latitude;
      const lon = ph.longitude;
      const phName = escapeHtml(ph.pharmacy_name);

      return `
        <div class="card" style="margin-bottom: 10px;">
          <div class="card-header">
            <div>
              <div class="card-title">${phName}</div>
              <div class="card-meta">${escapeHtml(ph.locality || '')}</div>
            </div>
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 4px;">
              ${statusBadge}
              ${freshnessHtml}
            </div>
          </div>
          <div style="margin-top: 8px;">
            ${qtyStr}
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--slate-100);">
            <span style="font-size: 0.75rem; color: var(--slate-600); font-weight: 600;">${distStr}</span>
            <button class="btn btn-secondary btn-sm" onclick="openDirections(${lat}, ${lon}, '${phName}')">
              Directions ↗
            </button>
          </div>
        </div>`;
    }).join('');

  } catch (err) {
    container.innerHTML = `
      <div class="empty-state">
        <div style="color: var(--danger); font-weight: 600; margin-bottom: 6px;">Failed to Load Availability</div>
        <p style="font-size: 0.8rem; color: var(--slate-600); margin-bottom: 12px;">${escapeHtml(err.message)}</p>
        <div style="display: flex; gap: 8px; justify-content: center;">
          <button class="btn btn-primary btn-sm" onclick="loadSubstitutePharmacies(${medicineId})">
            ↻ Retry
          </button>
          <button class="btn btn-secondary btn-sm" onclick="backFromSubstituteAvailability()">
            ← Back to Substitutes
          </button>
        </div>
      </div>`;
  }
}

function requestGpsForSubstitutes() {
  useCurrentLocation();
}

function searchStockForMedicine(medicineId, brandName) {
  selectSubstitute(medicineId, brandName, '', '', 'Tablet', false);
}

function openDirections(lat, lon, name) {
  if (lat && lon) {
    const url = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  } else {
    showToast(`Location coordinates not available for ${name}`, 'warning');
  }
}

// ================= CHEMIST MODULE: CONTEXT & PHARMACY SWITCHING =================

async function loadChemistPharmacies() {
  try {
    const res = await fetch(`${API_BASE}/users/${state.chemistUserId}/pharmacies`);
    if (!res.ok) throw new Error('Could not load user pharmacies');
    const pharmacies = await res.json();
    state.userPharmacies = pharmacies;

    const selector = document.getElementById('pharmacy-selector');
    if (pharmacies.length > 0) {
      selector.innerHTML = pharmacies.map(p => `
        <option value="${p.id}">${escapeHtml(p.name)}</option>
      `).join('');

      state.activePharmacyId = pharmacies[0].id;
      state.activePharmacyName = pharmacies[0].name;
    } else {
      // Fallback: fetch general nearby pharmacies to populate selector
      const fallbackRes = await fetch(`${API_BASE}/pharmacies/nearby?lat=28.5244&lon=77.2066&radius_km=50`);
      const fallbackList = await fallbackRes.json();
      if (fallbackList && fallbackList.length > 0) {
        selector.innerHTML = fallbackList.map(p => `
          <option value="${p.id}">${escapeHtml(p.name)}</option>
        `).join('');
        state.activePharmacyId = fallbackList[0].id;
        state.activePharmacyName = fallbackList[0].name;
      }
    }
  } catch (err) {
    console.error('Error loading chemist stores:', err);
  }
}

function onPharmacyChange() {
  const selector = document.getElementById('pharmacy-selector');
  state.activePharmacyId = parseInt(selector.value, 10);
  const selectedOpt = selector.options[selector.selectedIndex];
  state.activePharmacyName = selectedOpt ? selectedOpt.text : '';

  showToast(`Switched active pharmacy to ${state.activePharmacyName}`, 'success');
  updateProposalBadgeCount();
}

// ================= CHEMIST MODULE: NLP STOCK PROPOSAL =================

function fillNlp(text) {
  document.getElementById('nlp-input').value = text;
}

async function submitStockProposal() {
  const textarea = document.getElementById('nlp-input');
  const rawText = textarea.value.trim();
  const resultContainer = document.getElementById('nlp-update-result');

  if (!rawText) {
    showToast('Please type a stock update message first', 'warning');
    return;
  }

  if (!state.activePharmacyId) {
    showToast('No active pharmacy selected', 'error');
    return;
  }

  const btn = document.getElementById('btn-submit-nlp');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Processing`;
  resultContainer.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/proposals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        raw_message: rawText,
        chemist_user_id: state.chemistUserId,
        pharmacy_id: state.activePharmacyId
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to process proposal');

    if (data.status === 'CREATED' && data.proposals && data.proposals.length > 0) {
      showToast('Proposal created! Human confirmation required.', 'warning');
      textarea.value = '';

      resultContainer.innerHTML = `
        <div class="card" style="border-left: 4px solid var(--secondary); background: #f0fdf4;">
          <div style="font-weight: 700; color: var(--primary); margin-bottom: 4px;">
            ✓ Interpretation Ready for Review
          </div>
          <p style="font-size: 0.8rem; color: var(--slate-600); margin-bottom: 10px;">
            The inventory has NOT been changed yet. Please review the pending proposal in the Proposals tab to confirm or reject.
          </p>
          <button class="btn btn-primary" onclick="navigateTo('view-chemist-proposals')">
            Go to Pending Proposals (${data.proposals.length})
          </button>
        </div>`;

      updateProposalBadgeCount();

    } else if (data.status === 'DISAMBIGUATION_REQUIRED') {
      resultContainer.innerHTML = `
        <div class="card" style="border-left: 4px solid var(--warning);">
          <div style="font-weight: 700; color: var(--warning); margin-bottom: 4px;">
            ⚠️ Multiple Matches Found
          </div>
          <p style="font-size: 0.8rem; color: var(--slate-600); margin-bottom: 8px;">
            Please select the specific medicine:
          </p>
          <div style="display: flex; gap: 6px; flex-wrap: wrap;">
            ${(data.unresolved_items || []).flatMap(it => it.candidates || []).map(c => `
              <button class="chip" onclick="fillNlp('${escapeHtml(c.brand_name)} in stock')">${escapeHtml(c.brand_name)}</button>
            `).join('')}
          </div>
        </div>`;

    } else {
      resultContainer.innerHTML = `
        <div class="card" style="border-left: 4px solid var(--danger);">
          <div style="font-weight: 700; color: var(--danger);">Could not resolve medicine</div>
          <p style="font-size: 0.8rem; color: var(--slate-600); margin-top: 4px;">
            No matching brand or generic salt found for "${escapeHtml(rawText)}".
          </p>
        </div>`;
    }

  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Process Update';
  }
}

// ================= CHEMIST MODULE: PENDING PROPOSALS & CONFIRMATION =================

async function updateProposalBadgeCount() {
  if (!state.activePharmacyId) return;
  try {
    const res = await fetch(`${API_BASE}/proposals?pharmacy_id=${state.activePharmacyId}&status=PENDING`);
    if (res.ok) {
      const proposals = await res.json();
      const badge = document.getElementById('nav-proposal-badge');
      if (proposals.length > 0) {
        badge.textContent = proposals.length;
        badge.style.display = 'block';
      } else {
        badge.style.display = 'none';
      }
    }
  } catch (e) {
    // Non-blocking
  }
}

async function loadPendingProposals() {
  const container = document.getElementById('proposals-list');
  if (!state.activePharmacyId) {
    container.innerHTML = `<div class="empty-state"><p>Please select a pharmacy first.</p></div>`;
    return;
  }

  container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Loading pending proposals...</p></div>`;

  try {
    const res = await fetch(`${API_BASE}/proposals?pharmacy_id=${state.activePharmacyId}&status=PENDING`);
    if (!res.ok) throw new Error('Failed to fetch proposals');
    const proposals = await res.json();

    if (!proposals || proposals.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">✓</div>
          <p>No pending stock proposals awaiting confirmation.</p>
        </div>`;
      updateProposalBadgeCount();
      return;
    }

    container.innerHTML = proposals.map(p => {
      const confidencePct = Math.round((p.confidence_score || 1.0) * 100);
      const isFlagged = p.flagged_for_review;
      const reviewNotice = isFlagged
        ? `<div class="badge badge-schedule-h" style="margin-top: 4px;">⚠️ Review Flagged (Confidence < 80% or Schedule H)</div>`
        : '';

      const expiryText = p.expires_at ? `Expires in ~24h` : '';

      return `
        <div class="card proposal-card ${isFlagged ? 'flagged' : ''}" id="proposal-card-${p.id}">
          <div class="card-header">
            <div>
              <div class="card-title">${escapeHtml(p.medicine_brand_name || 'Medicine #' + p.medicine_id)}</div>
              <div class="card-meta">Proposed: <strong>${escapeHtml(p.proposed_status)}</strong> ${p.proposed_quantity != null ? `(${p.proposed_quantity} units)` : ''}</div>
            </div>
            <span class="badge badge-pending">PENDING</span>
          </div>

          <div style="font-size: 0.75rem; color: var(--slate-500); display: flex; justify-content: space-between;">
            <span>Parse Confidence: <strong>${confidencePct}%</strong></span>
            <span>${expiryText}</span>
          </div>
          <div class="proposal-confidence-bar">
            <div class="proposal-confidence-fill" style="width: ${confidencePct}%;"></div>
          </div>

          ${reviewNotice}

          <div class="proposal-actions">
            <button class="btn btn-success" style="flex: 1;" onclick="confirmProposal(${p.id})">
              ✓ Confirm & Apply
            </button>
            <button class="btn btn-danger" onclick="rejectProposal(${p.id})">
              ✕ Reject
            </button>
          </div>
        </div>`;
    }).join('');

    updateProposalBadgeCount();

  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p style="color: var(--danger);">${escapeHtml(err.message)}</p></div>`;
  }
}

async function confirmProposal(proposalId) {
  const card = document.getElementById(`proposal-card-${proposalId}`);
  if (card) {
    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
  }

  try {
    const res = await fetch(`${API_BASE}/proposals/${proposalId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chemist_user_id: state.chemistUserId,
        pharmacy_id: state.activePharmacyId
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Confirmation failed');

    showToast('Stock update confirmed and applied!', 'success');
    loadPendingProposals();
  } catch (err) {
    showToast(err.message, 'error');
    if (card) {
      card.style.opacity = '1';
      card.style.pointerEvents = 'auto';
    }
  }
}

async function rejectProposal(proposalId) {
  try {
    const res = await fetch(`${API_BASE}/proposals/${proposalId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chemist_user_id: state.chemistUserId,
        pharmacy_id: state.activePharmacyId,
        rejection_reason: 'Rejected by chemist via mobile UI'
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Rejection failed');

    showToast('Proposal rejected without changing stock', 'warning');
    loadPendingProposals();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ================= CHEMIST MODULE: STORE INVENTORY & STALENESS =================

async function loadPharmacyInventory() {
  const container = document.getElementById('inventory-list');
  if (!state.activePharmacyId) {
    container.innerHTML = `<div class="empty-state"><p>Please select a pharmacy first.</p></div>`;
    return;
  }

  container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Loading inventory...</p></div>`;

  try {
    const res = await fetch(`${API_BASE}/pharmacies/${state.activePharmacyId}/inventory`);
    if (!res.ok) throw new Error('Failed to fetch inventory');
    const items = await res.json();
    state.rawInventory = items;

    renderInventory(items);
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p style="color: var(--danger);">${escapeHtml(err.message)}</p></div>`;
  }
}

function filterInventoryList() {
  const q = document.getElementById('inventory-filter-input').value.trim().toLowerCase();
  if (!q) {
    renderInventory(state.rawInventory);
    return;
  }
  const filtered = state.rawInventory.filter(item =>
    (item.brand_name && item.brand_name.toLowerCase().includes(q)) ||
    (item.generic_salt && item.generic_salt.toLowerCase().includes(q))
  );
  renderInventory(filtered);
}

function renderInventory(items) {
  const container = document.getElementById('inventory-list');
  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">📦</div>
        <p>No inventory records found for this store.</p>
      </div>`;
    return;
  }

  container.innerHTML = items.map(it => {
    let statusBadge = '<span class="badge badge-in-stock">IN STOCK</span>';
    if (it.stock_status === 'LOW_STOCK') statusBadge = '<span class="badge badge-low-stock">LOW STOCK</span>';
    if (it.stock_status === 'OUT_OF_STOCK') statusBadge = '<span class="badge badge-out-of-stock">OUT OF STOCK</span>';

    const staleBadge = it.is_stale
      ? `<span class="badge badge-stale" title="Unconfirmed for >24h">⚠️ Stale (>24h)</span>`
      : `<span style="font-size:0.7rem; color:var(--success);">● Verified Fresh</span>`;

    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(it.brand_name || 'Medicine #' + it.medicine_id)}</div>
            <div class="card-meta">${escapeHtml(it.generic_salt || '')} • ${escapeHtml(it.strength || '')}</div>
          </div>
          ${statusBadge}
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; color: var(--slate-500); margin-top: 6px;">
          <span>Qty: <strong>${it.quantity != null ? it.quantity : 'N/A'}</strong></span>
          ${staleBadge}
        </div>
      </div>`;
  }).join('');
}

// ================= CHEMIST MODULE: AUDIT HISTORY & ONE-CLICK UNDO =================

async function loadPharmacyHistory() {
  const container = document.getElementById('history-list');
  if (!state.activePharmacyId) {
    container.innerHTML = `<div class="empty-state"><p>Please select a pharmacy first.</p></div>`;
    return;
  }

  container.innerHTML = `<div class="empty-state"><div class="spinner"></div><p style="margin-top:10px;">Loading audit trail...</p></div>`;

  try {
    const res = await fetch(`${API_BASE}/pharmacies/${state.activePharmacyId}/history?limit=30`);
    if (!res.ok) throw new Error('Failed to fetch history');
    const records = await res.json();

    if (!records || records.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📜</div>
          <p>No audit trail records found for this store.</p>
        </div>`;
      return;
    }

    container.innerHTML = records.map((rec, idx) => {
      const isUndoType = rec.change_type === 'UNDO';
      const badge = isUndoType
        ? `<span class="badge" style="background:#fef3c7; color:#92400e;">ROLLBACK (UNDO)</span>`
        : `<span class="badge badge-in-stock">UPDATE</span>`;

      // Allow Undo on recent regular update that has not been undone
      const canShowUndoBtn = !isUndoType && !rec.is_undone && idx === 0;

      return `
        <div class="card" style="border-left: 3px solid ${isUndoType ? 'var(--warning)' : 'var(--primary)'};">
          <div class="card-header">
            <div>
              <div class="card-title" style="font-size: 0.85rem;">Medicine #${rec.medicine_id}</div>
              <div class="card-meta">${escapeHtml(rec.timestamp ? new Date(rec.timestamp).toLocaleString() : '')}</div>
            </div>
            ${badge}
          </div>
          <div style="font-size: 0.75rem; color: var(--slate-600); margin: 6px 0;">
            Status: ${rec.previous_status || 'INIT'} → <strong>${rec.new_status}</strong><br>
            Quantity: ${rec.previous_quantity != null ? rec.previous_quantity : 0} → <strong>${rec.new_quantity != null ? rec.new_quantity : 'N/A'}</strong>
          </div>
          ${canShowUndoBtn ? `
            <div style="margin-top: 8px;">
              <button class="btn btn-danger" style="padding: 4px 10px; font-size: 0.75rem;" onclick="undoHistoryRecord(${rec.id})">
                ↩ Undo This Change
              </button>
            </div>
          ` : ''}
        </div>`;
    }).join('');

  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p style="color: var(--danger);">${escapeHtml(err.message)}</p></div>`;
  }
}

async function undoHistoryRecord(historyId) {
  try {
    const res = await fetch(`${API_BASE}/history/${historyId}/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chemist_user_id: state.chemistUserId,
        pharmacy_id: state.activePharmacyId
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Undo failed');

    showToast('Inventory change rolled back successfully!', 'success');
    loadPharmacyHistory();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ================= TOAST NOTIFICATION UTILITY =================

function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 3500);
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
