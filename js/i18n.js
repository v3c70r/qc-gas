export const translations = {
  'zh-Hans': {
    searchRadius: '搜索半径',
    priceRange: '价格区间',
    brand: '品牌',
    fuelType: '燃油类型',
    regular: '普通',
    super: '高级',
    diesel: '柴油',
    region: '地区',
    allRegions: '所有地区',
    statsTitle: '半径统计',
    stations: '加油站',
    stationsSorted: '加油站 (按价格排序)',
    currentLocation: '📍 我的位置',
    resetView: '↺ 重置',
    dataLoading: '数据: 加载中...',
    loading: '加载数据中...',
    pricePerLitre: '每升价格',
    low: '低 (≤1.70$)',
    high: '高 (≥1.95$)',
    title: '⛽ 魁北克油价',
    noData: '无数据',
    loadingError: '加载错误',
    priceLabel: '$/L'
  },
  'en-CA': {
    searchRadius: 'Search radius',
    priceRange: 'Price range',
    brand: 'Brand',
    fuelType: 'Fuel type',
    regular: 'Regular',
    super: 'Premium',
    diesel: 'Diesel',
    region: 'Region',
    allRegions: 'All regions',
    statsTitle: 'Radius statistics',
    stations: 'Stations',
    stationsSorted: 'Stations (sorted by price)',
    currentLocation: '📍 My location',
    resetView: '↺ Reset',
    dataLoading: 'Data: loading...',
    loading: 'Loading data...',
    pricePerLitre: 'Price per litre',
    low: 'Low (≤1.70$)',
    high: 'High (≥1.95$)',
    title: '⛽ Québec Gas Prices',
    noData: 'No data',
    loadingError: 'Loading error',
    priceLabel: '$/L'
  },
  'fr-CA': {
    searchRadius: 'Rayon de recherche',
    priceRange: 'Plage de prix',
    brand: 'Marque',
    fuelType: 'Type de carburant',
    regular: 'Régulier',
    super: 'Super',
    diesel: 'Diesel',
    region: 'Région',
    allRegions: 'Tous les régions',
    statsTitle: 'Statistiques du rayon',
    stations: 'Stations',
    stationsSorted: 'Stations (trié par prix)',
    currentLocation: '📍 Ma position',
    resetView: '↺ Réinitialiser',
    dataLoading: 'Données: en chargement...',
    loading: 'Chargement des données...',
    pricePerLitre: 'Prix au litre',
    low: 'Bas (≤1.70$)',
    high: 'Élevé (≥1.95$)',
    title: '⛽ Prix de l\'essence Québec',
    noData: 'Pas de données',
    loadingError: 'Erreur de chargement',
    priceLabel: '$/L'
  }
};

export const languages = [
  { code: 'zh-Hans', name: '中文', flag: '🇨🇳' },
  { code: 'en-CA', name: 'English', flag: '🇨🇦' },
  { code: 'fr-CA', name: 'Français', flag: '🇨🇦' }
];

export function getStoredLanguage() {
  const stored = localStorage.getItem('language');
  if (stored && translations[stored]) return stored;
  const browserLang = navigator.language || navigator.userLanguage;
  if (browserLang.startsWith('zh')) return 'zh-Hans';
  if (browserLang.startsWith('en')) return 'en-CA';
  return 'fr-CA';
}

export function setLanguage(code) {
  if (!translations[code]) return;
  localStorage.setItem('language', code);
  document.documentElement.lang = code;
  applyTranslations(code);
}

export function applyTranslations(code) {
  const t = translations[code];
  if (!t) return;
  
  const el = id => document.getElementById(id);
  
  if (el('search-radius-label')) el('search-radius-label').textContent = t.searchRadius;
  if (el('price-range-label')) el('price-range-label').textContent = t.priceRange;
  if (el('brand-label')) el('brand-label').textContent = t.brand;
  if (el('fuel-type-label')) el('fuel-type-label').textContent = t.fuelType;
  if (el('region-label')) el('region-label').textContent = t.region;
  if (el('stats-title')) el('stats-title').textContent = t.statsTitle;
  if (el('station-list-label')) el('station-list-label').textContent = t.stationsSorted;
  if (el('title')) el('title').textContent = t.title;
  if (el('current-location')) el('current-location').textContent = t.currentLocation;
  if (el('reset-view')) el('reset-view').textContent = t.resetView;
  if (el('data-status')) el('data-status').textContent = t.dataLoading;
  if (el('loading-text')) el('loading-text').textContent = t.loading;
  if (el('legend-title')) el('legend-title').textContent = t.pricePerLitre;
  if (el('legend-low')) el('legend-low').textContent = t.low;
  if (el('legend-high')) el('legend-high').textContent = t.high;
  
  document.querySelectorAll('.stat-label[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (t[key]) el.textContent = t[key];
  });
  
  document.querySelectorAll('.fuel-filter-label').forEach(el => {
    const fuel = el.dataset.fuel;
    if (fuel === 'regular') el.textContent = t.regular;
    if (fuel === 'super') el.textContent = t.super;
    if (fuel === 'diesel') el.textContent = t.diesel;
  });
  
  const regionSelect = document.getElementById('region-filter');
  if (regionSelect && regionSelect.firstElementChild) {
    regionSelect.firstElementChild.textContent = t.allRegions;
  }
}

export function createLanguageSelector(currentLang) {
  const container = document.getElementById('language-selector');
  if (!container) return;
  
  container.innerHTML = languages.map(lang => 
    `<button class="lang-btn ${lang.code === currentLang ? 'active' : ''}" data-lang="${lang.code}">${lang.flag} ${lang.name}</button>`
  ).join('');
  
  container.querySelectorAll('.lang-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      setLanguage(btn.dataset.lang);
      container.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
}