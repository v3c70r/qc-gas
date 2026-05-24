import { updateStats, updateStationList } from './stats.js';
import { map, MONTREAL_CENTER, rangeRadius, addRangeCircle } from './map.js';
import { t } from './i18n.js';

// ── Debounce helper ──
function debounce(fn, delay) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

let filterPanelOpen = false;

function initFilters() {
  const filterToggle = document.getElementById('filter-toggle');
  const filterPanel = document.getElementById('filter-panel');

  filterToggle.addEventListener('click', () => {
    filterPanelOpen = !filterPanelOpen;
    filterToggle.classList.toggle('open', filterPanelOpen);
    filterPanel.classList.toggle('open', filterPanelOpen);
  });

  // Brand filter changes
  document.addEventListener('change', (e) => {
    if (e.target.classList.contains('brand-filter')) {
      const item = e.target.closest('.brand-filter-item');
      if (item) item.classList.toggle('active', e.target.checked);
      updateStats();
    }
    if (e.target.classList.contains('fuel-filter')) {
      const chip = e.target.closest('.fuel-chip');
      if (chip) chip.classList.toggle('active', e.target.checked);
      updateStats();
    }
  });

  document.getElementById('region-filter').addEventListener('change', () => {
    updateStats();
  });

  const minSlider = document.getElementById('min-price');
  const maxSlider = document.getElementById('max-price');
  const minVal = document.getElementById('min-price-val');
  const maxVal = document.getElementById('max-price-val');

  function updatePriceLabels() {
    let min = parseInt(minSlider.value);
    let max = parseInt(maxSlider.value);
    if (min > max) [min, max] = [max, min];
    minVal.textContent = (min / 100).toFixed(2) + '$';
    maxVal.textContent = (max / 100).toFixed(2) + '$';
    updateStats();
  }

  const debouncedPriceUpdate = debounce(updatePriceLabels, 150);
  minSlider.addEventListener('input', debouncedPriceUpdate);
  maxSlider.addEventListener('input', debouncedPriceUpdate);

  const showMoreBtn = document.getElementById('show-more-brands');
  const moreBrands = document.getElementById('more-brands');
  showMoreBtn.addEventListener('click', () => {
    const isHidden = moreBrands.style.display === 'none';
    moreBrands.style.display = isHidden ? 'block' : 'none';
    showMoreBtn.textContent = isHidden ? t('showLessBrands') : t('showMoreBrands');
  });

  document.querySelectorAll('.radius-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.radius-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      // Sync the shared radius variable and redraw the circle on the map
      const newRadius = parseFloat(btn.dataset.radius);
      rangeRadius.value = newRadius;
      addRangeCircle();
      updateStats();
    });
  });
}

function initGeolocation() {
  const btn = document.getElementById('locate-btn');

  btn.addEventListener('click', () => {
    if (!navigator.geolocation) {
      alert(t('geolocationError'));
      return;
    }

    btn.disabled = true;
    btn.style.opacity = '0.6';

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        MONTREAL_CENTER[0] = longitude;
        MONTREAL_CENTER[1] = latitude;
        map.flyTo({ center: [longitude, latitude], zoom: 13, duration: 1200 });
        btn.disabled = false;
        btn.style.opacity = '1';
        setTimeout(() => updateStats(), 1300);
      },
      (error) => {
        btn.disabled = false;
        btn.style.opacity = '1';
        const messages = {
          1: t('geolocationDenied'),
          2: t('geolocationUnavailable'),
          3: t('geolocationTimeout')
        };
        alert(messages[error.code] || t('geolocationError'));
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 300000 }
    );
  });
}

function initSidebarToggle() {
  const sidebar = document.getElementById('sidebar');
  let startY = 0;
  let startTransform = 0;

  const handle = document.getElementById('sidebar-handle');

  handle.addEventListener('touchstart', (e) => {
    startY = e.touches[0].clientY;
    const isCollapsed = sidebar.classList.contains('collapsed');
    startTransform = isCollapsed ? sidebar.offsetHeight - 60 : 0;
  }, { passive: true });

  handle.addEventListener('touchmove', (e) => {
    const deltaY = startY - e.touches[0].clientY;
    const newTransform = Math.max(0, Math.min(sidebar.offsetHeight - 60, startTransform + deltaY));
    sidebar.style.transform = `translateY(${sidebar.offsetHeight - 60 - newTransform}px)`;
  }, { passive: true });

  handle.addEventListener('touchend', () => {
    const currentTransform = sidebar.style.transform;
    const match = currentTransform.match(/translateY\(([0-9.-]+)px\)/);
    const currentY = match ? parseFloat(match[1]) : 0;
    if (currentY > 30) {
      sidebar.classList.remove('collapsed');
    } else {
      sidebar.classList.add('collapsed');
    }
    sidebar.style.transform = '';
  });
}

export { initFilters, initGeolocation, initSidebarToggle };