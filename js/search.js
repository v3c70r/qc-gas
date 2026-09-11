// ── Offline station search ──
// Pure normalization + matching helpers (unit-testable), plus the sidebar
// search box wiring. Matching is intentionally local-only: no geocoding API,
// no new network requests.

import { map, currentStations } from './map.js';
import { updateStats, showPopup } from './stats.js';
import { t, onLanguageChange } from './i18n.js';
import { brandColor, brandAbbr } from './constants.js';

let currentQuery = '';
let suggestions = [];
let suggestionIndex = -1;

// Normalize for accent-insensitive, case-insensitive, whitespace-tolerant and
// punctuation-tolerant matching. Non-alphanumerics become a single space so
// "Rouyn-Noranda" and "Rouyn Noranda" behave the same.
export function normalizeText(value) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Match a station Feature against an already-normalized query. Searches
// station name, brand, address, postal code and region.
export function matchesFeature(feature, normalizedQuery) {
  if (!feature?.properties || !normalizedQuery) return false;
  const p = feature.properties;
  const fields = [p.name, p.brand, p.address, p.postal_code, p.region];
  return fields.some((field) => normalizeText(field).includes(normalizedQuery));
}

// Return matching features for a raw query. Queries shorter than 2 chars
// intentionally return nothing (acceptance: input >= 2 chars).
export function searchFeatures(query, features) {
  const normalizedQuery = normalizeText(query);
  if (normalizedQuery.length < 2) return [];
  return (Array.isArray(features) ? features : []).filter((feature) =>
    matchesFeature(feature, normalizedQuery)
  );
}

export function isSearchActive() {
  return normalizeText(currentQuery).length >= 2;
}

export function getSearchQuery() {
  return currentQuery;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function hideSuggestions() {
  const box = document.getElementById('search-suggestions');
  if (box) box.hidden = true;
  const input = document.getElementById('station-search');
  if (input) input.setAttribute('aria-expanded', 'false');
  suggestionIndex = -1;
}

function updateActiveSuggestion() {
  document.querySelectorAll('#search-suggestions .search-suggestion').forEach((el, i) => {
    const active = i === suggestionIndex;
    el.classList.toggle('active', active);
    el.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function moveSuggestion(delta) {
  if (suggestions.length === 0) return;
  suggestionIndex = (suggestionIndex + delta + suggestions.length) % suggestions.length;
  updateActiveSuggestion();
}

function confirmSelection() {
  const feature = suggestions[suggestionIndex] || suggestions[0];
  if (!feature) return;
  hideSuggestions();

  const coords = feature.geometry?.coordinates;
  if (map?.flyTo && coords) {
    map.flyTo({ center: coords, zoom: 15, duration: 800 });
  }
  showPopup(feature);
}

function renderSuggestions() {
  const box = document.getElementById('search-suggestions');
  if (!box) return;

  if (!isSearchActive()) {
    hideSuggestions();
    return;
  }

  const features = Array.isArray(currentStations?.features) ? currentStations.features : [];
  suggestions = searchFeatures(currentQuery, features).slice(0, 8);
  suggestionIndex = suggestions.length ? 0 : -1;

  box.innerHTML = '';
  if (suggestions.length === 0) {
    hideSuggestions();
    return;
  }

  suggestions.forEach((feature, i) => {
    const props = feature.properties;
    const item = document.createElement('div');
    item.className = 'search-suggestion' + (i === suggestionIndex ? ' active' : '');
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', i === suggestionIndex ? 'true' : 'false');
    item.innerHTML = `
      <span class="ss-icon" style="background:${brandColor(props.brand)}">${escapeHtml(brandAbbr(props.brand))}</span>
      <span class="ss-info">
        <span class="ss-name">${escapeHtml(props.name || props.brand)}</span>
        <span class="ss-details">${escapeHtml(props.address || props.region || '')}</span>
      </span>`;
    item.addEventListener('mousedown', (e) => e.preventDefault());
    item.addEventListener('click', () => {
      suggestionIndex = i;
      confirmSelection();
    });
    box.appendChild(item);
  });

  box.hidden = false;
  const input = document.getElementById('station-search');
  if (input) input.setAttribute('aria-expanded', 'true');
}

function setQuery(value) {
  currentQuery = value;
  const input = document.getElementById('station-search');
  const clearBtn = document.getElementById('search-clear');
  if (input && input.value !== value) input.value = value;
  if (clearBtn) clearBtn.hidden = value.length === 0;
  updateStats();
}

export function clearSearch() {
  setQuery('');
  hideSuggestions();
}

function updateSearchLabels() {
  const btn = document.getElementById('search-clear');
  if (btn) {
    btn.setAttribute('aria-label', t('searchClear'));
    btn.setAttribute('title', t('searchClear'));
  }
  const input = document.getElementById('station-search');
  if (input) input.setAttribute('aria-label', t('searchPlaceholder'));
}

export function initSearch() {
  const input = document.getElementById('station-search');
  const clearBtn = document.getElementById('search-clear');
  if (!input || !clearBtn) return;

  input.addEventListener('input', () => {
    setQuery(input.value);
    renderSuggestions();
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      moveSuggestion(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      moveSuggestion(-1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      confirmSelection();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      clearSearch();
      input.blur();
    }
  });

  clearBtn.addEventListener('click', () => {
    clearSearch();
    input.focus();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#search-box')) hideSuggestions();
  });

  onLanguageChange(updateSearchLabels);
  updateSearchLabels();
}

// Test/console hooks (mirrors window.__qcGasMap).
window.__qcGasSearch = {
  normalizeText,
  matchesFeature,
  searchFeatures,
  isActive: isSearchActive,
  clear: clearSearch,
  getQuery: () => currentQuery
};
