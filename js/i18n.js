export const translations = {
  'zh-Hans': {
    results: '结果',
    stations_count: '{n} 个加油站',
    filters: '筛选',
    brand: '品牌',
    fuel: '燃油类型',
    priceRange: '价格区间 (¢/L)',
    region: '地区',
    radius: '半径',
    stations: '加油站',
    sortByPrice: '按价格排序',
    noStations: '未找到加油站',
    regular: '普通',
    super: '高级',
    diesel: '柴油',
    allRegions: '全部',
    showMoreBrands: '+ 更多品牌',
    showLessBrands: '− 收起',
    title: '⛽ 魁北克油价',
    dataLoading: '数据: 加载中...',
    dataLoaded: '数据: {n} 个加油站',
    loading: '加载中...',
    lowPrice: '低价 (≤1.65$)',
    highPrice: '高价 (≥2.00$)',
    locateMe: '定位',
    reset: '重置',
    geolocationError: '无法获取您的位置',
    geolocationDenied: '位置访问被拒绝。请在浏览器设置中开启。',
    geolocationUnavailable: '位置不可用，请重试。',
    geolocationTimeout: '请求超时，请重试。',
    station: '加油站',
    address: '地址',
    loadingError: '数据加载错误',
    cheapest: '最低价',
    km: '公里',
    pricePerLitre: '每升价格',
    minPrice: '最低价',
    maxPrice: '最高价',
    mapError: '地图加载失败',
    noToken: '请在 .env 文件中设置 VITE_MAPBOX_ACCESS_TOKEN',
    selectAllBrands: '全选',
    deselectAllBrands: '取消全选',
    navigate: '导航',
    dataUpdated: '更新于 {time}'
  },
  'en-CA': {
    results: 'Results',
    stations_count: '{n} stations',
    filters: 'Filters',
    brand: 'Brand',
    fuel: 'Fuel type',
    priceRange: 'Price range (¢/L)',
    region: 'Region',
    radius: 'Radius',
    stations: 'Stations',
    sortByPrice: 'sorted by price',
    noStations: 'No stations found',
    regular: 'Regular',
    super: 'Premium',
    diesel: 'Diesel',
    allRegions: 'All',
    showMoreBrands: '+ More brands',
    showLessBrands: '− Less',
    title: '⛽ Québec Gas Prices',
    dataLoading: 'Data: loading...',
    dataLoaded: 'Data: {n} stations',
    loading: 'Loading...',
    lowPrice: 'Low (≤1.65$)',
    highPrice: 'High (≥2.00$)',
    locateMe: 'Locate',
    reset: 'Reset',
    geolocationError: 'Geolocation is not supported by your browser',
    geolocationDenied: 'Location access denied. Please enable it in browser settings.',
    geolocationUnavailable: 'Location unavailable. Please try again.',
    geolocationTimeout: 'Request timed out. Please try again.',
    station: 'Station',
    address: 'Address',
    loadingError: 'Data loading error',
    cheapest: 'Best price',
    km: 'km',
    pricePerLitre: 'Price per litre',
    minPrice: 'Min',
    maxPrice: 'Max',
    mapError: 'Map failed to load',
    noToken: 'Please set VITE_MAPBOX_ACCESS_TOKEN in your .env file',
    selectAllBrands: 'Select all',
    deselectAllBrands: 'Deselect all',
    navigate: 'Navigate',
    dataUpdated: 'Updated {time}'
  },
  'fr-CA': {
    results: 'Résultats',
    stations_count: '{n} stations',
    filters: 'Filtres',
    brand: 'Marque',
    fuel: 'Type de carburant',
    priceRange: 'Plage de prix (¢/L)',
    region: 'Région',
    radius: 'Rayon',
    stations: 'Stations',
    sortByPrice: 'trié par prix',
    noStations: 'Aucune station trouvée',
    regular: 'Régulier',
    super: 'Super',
    diesel: 'Diesel',
    allRegions: 'Toutes',
    showMoreBrands: '+ Plus de marques',
    showLessBrands: '− Moins',
    title: '⛽ Prix de l\'essence Québec',
    dataLoading: 'Données: en chargement...',
    dataLoaded: 'Données: {n} stations',
    loading: 'Chargement...',
    lowPrice: 'Bas (≤1.65$)',
    highPrice: 'Élevé (≥2.00$)',
    locateMe: 'Localiser',
    reset: 'Réinitialiser',
    geolocationError: 'Geolocation n\'est pas supporté par votre navigateur',
    geolocationDenied: 'Accès à la localisation refusé. Veuillez l\'activer dans les paramètres.',
    geolocationUnavailable: 'Position non disponible. Veuillez réessayer.',
    geolocationTimeout: 'Délai dépassé. Veuillez réessayer.',
    station: 'Station',
    address: 'Adresse',
    loadingError: 'Erreur de chargement des données',
    cheapest: 'Meilleur prix',
    km: 'km',
    pricePerLitre: 'Prix au litre',
    minPrice: 'Min',
    maxPrice: 'Max',
    mapError: 'Échec du chargement de la carte',
    noToken: 'Veuillez définir VITE_MAPBOX_ACCESS_TOKEN dans votre fichier .env',
    selectAllBrands: 'Tout sélectionner',
    deselectAllBrands: 'Tout désélectionner',
    navigate: 'Itinéraire',
    dataUpdated: 'Mis à jour le {time}'
  }
};

export const languages = [
  { code: 'en-CA', name: 'EN', flag: '' },
  { code: 'fr-CA', name: 'FR', flag: '' },
  { code: 'zh-Hans', name: '中文', flag: '' }
];

let currentLang = 'fr-CA';
const listeners = [];

export function getStoredLanguage() {
  const stored = localStorage.getItem('language');
  if (stored && translations[stored]) {
    currentLang = stored;
    return stored;
  }
  const browserLang = navigator.language || '';
  if (browserLang.startsWith('zh')) currentLang = 'zh-Hans';
  else if (browserLang.startsWith('en')) currentLang = 'en-CA';
  else currentLang = 'fr-CA';
  return currentLang;
}

export function getLanguage() {
  return currentLang;
}

export function setLanguage(code) {
  if (!translations[code]) return;
  currentLang = code;
  localStorage.setItem('language', code);
  document.documentElement.lang = code;
  applyTranslations(code);
  listeners.forEach(fn => fn(code));
}

export function onLanguageChange(fn) {
  listeners.push(fn);
}

export function t(key) {
  const dict = translations[currentLang];
  return dict && dict[key] ? dict[key] : key;
}

export function tf(key, vars) {
  let text = t(key);
  if (vars) {
    Object.keys(vars).forEach(k => {
      text = text.replace(`{${k}}`, vars[k]);
    });
  }
  return text;
}

export function applyTranslations(code) {
  const lang = code || currentLang;
  const dict = translations[lang];
  if (!dict) return;

  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (dict[key]) el.textContent = dict[key];
  });

  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.dataset.i18nPlaceholder;
    if (dict[key]) el.placeholder = dict[key];
  });

  const regionSelect = document.getElementById('region-filter');
  if (regionSelect && regionSelect.firstElementChild) {
    regionSelect.firstElementChild.textContent = dict.allRegions;
  }

  const fuelLabels = {
    'regular': dict.regular,
    'super': dict.super,
    'diesel': dict.diesel
  };
  document.querySelectorAll('.fuel-chip-label').forEach(el => {
    const fuel = el.dataset.fuel;
    if (fuelLabels[fuel]) el.textContent = fuelLabels[fuel];
  });

  const countEl = document.getElementById('sidebar-station-count');
  if (countEl && countEl.dataset.count !== undefined) {
    countEl.textContent = tf('stations_count', { n: countEl.dataset.count });
  }

  const noStationsEl = document.querySelector('#station-list > div[no-stations]');
  if (noStationsEl) noStationsEl.textContent = dict.noStations;
}

export function createLanguageSelector() {
  const existing = document.getElementById('lang-selector-wrap');
  if (existing) existing.remove();

  const wrap = document.createElement('div');
  wrap.id = 'lang-selector-wrap';
  wrap.style.cssText = 'display:flex;align-items:center;gap:8px;';

  const selector = document.createElement('div');
  selector.id = 'lang-selector';
  selector.style.cssText = 'display:flex;gap:0;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);';

  languages.forEach((lang, i) => {
    const btn = document.createElement('button');
    btn.textContent = lang.name;
    btn.dataset.lang = lang.code;
    btn.style.cssText = 'padding:5px 10px;border:none;background:transparent;color:#475569;font-size:12px;font-weight:500;cursor:pointer;transition:all 0.15s;border-radius:0;';
    btn.addEventListener('mouseenter', () => {
      if (!btn.classList.contains('active')) btn.style.background = '#e2e8f0';
    });
    btn.addEventListener('mouseleave', () => {
      if (!btn.classList.contains('active')) btn.style.background = 'transparent';
    });
    btn.addEventListener('click', () => setLanguage(lang.code));
    selector.appendChild(btn);

    // Divider between buttons
    if (i < languages.length - 1) {
      const divider = document.createElement('div');
      divider.style.cssText = 'width:1px;background:#e2e8f0;';
      selector.appendChild(divider);
    }
  });

  // Update button styles when language changes (in-place, no innerHTML)
  function updateLangButtons() {
    selector.querySelectorAll('button').forEach(btn => {
      const active = btn.dataset.lang === currentLang;
      btn.classList.toggle('active', active);
      btn.style.background = active ? '#fff' : 'transparent';
      btn.style.color = active ? '#1a73e8' : '#475569';
      btn.style.fontWeight = active ? '700' : '500';
    });
  }

  updateLangButtons();
  onLanguageChange(() => updateLangButtons());

  wrap.appendChild(selector);
  const header = document.getElementById('header');
  if (header) header.appendChild(wrap);
}