// content.js - extracts visible text from the page and sends it to the background/popup via chrome.storage
(function () {
  'use strict';

  const IDLE_CAPTURE_DELAY = 5000; // ms after activity stops
  const HEARTBEAT_BASE = 20000; // default heartbeat interval while idle (20s)
  const DEDUP_CHARS = 200; // first N chars used to dedupe
  const MAX_BACKOFF_MULT = 8;

  let lastCapturedHash = null;
  let activityTimer = null;
  let heartbeatTimer = null;
  let isUserActive = false;
  let backoffMult = 1;
  let cachedEndpoint = 'http://127.0.0.1:8000/analyze';

  // read cached endpoint once
  try {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(['analyzeEndpoint'], (res) => {
        if (res && res.analyzeEndpoint) cachedEndpoint = res.analyzeEndpoint;
      });
    }
  } catch (e) {}

  // update cached endpoint on change
  try {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'local' && changes.analyzeEndpoint) {
          cachedEndpoint = changes.analyzeEndpoint.newValue || cachedEndpoint;
        }
      });
    }
  } catch (e) {}

  function getSaveDataFactor() {
    try { return (navigator.connection && navigator.connection.saveData) ? 2 : 1; } catch (e) { return 1; }
  }

  let batteryFactor = 1;
  if (navigator.getBattery) {
    navigator.getBattery().then(batt => {
      function updateBattery() {
        batteryFactor = (!batt.charging && typeof batt.level === 'number' && batt.level < 0.2) ? 4 : 1;
      }
      updateBattery();
      if (batt.addEventListener) {
        batt.addEventListener('levelchange', updateBattery);
        batt.addEventListener('chargingchange', updateBattery);
      }
    }).catch(() => {});
  }

  function effectiveInterval() {
    return HEARTBEAT_BASE * backoffMult * getSaveDataFactor() * batteryFactor;
  }

  function elementInViewport(el) {
    try {
      const r = el.getBoundingClientRect();
      return r.bottom >= 0 && r.right >= 0 && r.top <= (window.innerHeight || document.documentElement.clientHeight) && r.left <= (window.innerWidth || document.documentElement.clientWidth);
    } catch (e) { return false; }
  }

  function visibleViewportText() {
    try {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
      let node = null;
      const chunks = [];
      while ((node = walker.nextNode())) {
        const txt = (node.nodeValue || '').trim();
        if (!txt) continue;
        const parent = node.parentElement;
        if (!parent) continue;
        const style = window.getComputedStyle(parent);
        if (style && (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity || '1') === 0)) continue;
        if (!elementInViewport(parent)) continue;
        chunks.push(txt);
      }
      return chunks.join(' ');
    } catch (e) { return ''; }
  }

  function postCapture(text) {
    if (!text) return;
    const endpoint = cachedEndpoint || 'http://127.0.0.1:8000/analyze';
    try {
      // Try to get user email + token from background (non-interactive). If found, include as user_id.
      chrome.runtime.sendMessage({ type: 'GET_USER_EMAIL', interactive: false }, async (resp) => {
        const headers = { 'Content-Type': 'application/json' };
        let body = { messages: [{ sender: 'page', text }], source: location.href };
        if (resp && resp.token) headers['Authorization'] = 'Bearer ' + resp.token;
        if (resp && resp.email) body.user_id = resp.email;
        try {
          const r = await fetch(endpoint, { method: 'POST', headers, body: JSON.stringify(body), keepalive: true });
          if (!r.ok) backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
          else backoffMult = 1;
        } catch (err) {
          backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
        } finally {
          scheduleHeartbeat();
        }
      });
    } catch (e) {
      backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
      scheduleHeartbeat();
    }
  }

  function captureNow() {
    if (document.visibilityState !== 'visible') return;
    const text = visibleViewportText();
    const hash = (text || '').slice(0, DEDUP_CHARS);
    if (!hash) return;
    if (hash === lastCapturedHash) return;
    lastCapturedHash = hash;
    try {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ lastCapturedText: text, capturedAt: new Date().toISOString() });
      }
      try { if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) chrome.runtime.sendMessage({ type: 'PAGE_TEXT_CAPTURED', text }); } catch (e) {}
    } catch (e) {}
    postCapture(text);
  }

  function onUserActivity() {
    isUserActive = true;
    if (activityTimer) clearTimeout(activityTimer);
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
    activityTimer = setTimeout(() => {
      isUserActive = false;
      captureNow();
      scheduleHeartbeat();
    }, IDLE_CAPTURE_DELAY);
  }

  function scheduleHeartbeat() {
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
    heartbeatTimer = setTimeout(() => {
      if (!isUserActive && document.visibilityState === 'visible') captureNow();
      scheduleHeartbeat();
    }, Math.max(5000, effectiveInterval()));
  }

  ['scroll', 'keydown', 'mousemove', 'touchstart'].forEach((evt) => {
    window.addEventListener(evt, onUserActivity, { passive: true });
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') {
      if (heartbeatTimer) clearTimeout(heartbeatTimer);
    } else {
      captureNow();
      scheduleHeartbeat();
    }
  });

  scheduleHeartbeat();
  setTimeout(() => { if (!isUserActive) captureNow(); }, 1000);
})();
// content.js - extracts visible text from the page and sends it to the background/popup via chrome.storage
(function () {
  // Configuration
  const IDLE_CAPTURE_DELAY = 5000; // ms after activity stops
  const HEARTBEAT_BASE = 20000; // default heartbeat interval while idle (20s)
  const DEDUP_CHARS = 200; // first N chars used to dedupe
  const MAX_BACKOFF_MULT = 8;

  // State
  let lastCapturedHash = null;
  let activityTimer = null;
  let heartbeatTimer = null;
  let isUserActive = false;
  let backoffMult = 1;
  let cachedEndpoint = 'http://127.0.0.1:8000/analyze';

  // initialize cachedEndpoint once at startup to avoid callbacks into invalidated extension context
  try {
    if (chrome && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(['analyzeEndpoint'], (res) => {
        if (res && res.analyzeEndpoint) cachedEndpoint = res.analyzeEndpoint;
      });
    }
  } catch (e) {
    // ignore
  }

  // keep cachedEndpoint updated if user changes it via popup
  try {
    if (chrome && chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'local' && changes.analyzeEndpoint) {
          cachedEndpoint = changes.analyzeEndpoint.newValue || cachedEndpoint;
        }
      });
    }
  } catch (e) { /* ignore */ }

  // Respect user/network hints
  function getSaveDataFactor() {
    try {
      const conn = navigator.connection || {};
      return conn.saveData ? 2 : 1;
    } catch (e) {
      return 1;
    }
  }

  let batteryFactor = 1;
  if (navigator.getBattery) {
    navigator.getBattery().then(batt => {
      function updateBattery() {
        if (!batt.charging && batt.level !== undefined && batt.level < 0.2) batteryFactor = 4;
        else batteryFactor = 1;
      }
      updateBattery();
      batt.addEventListener('levelchange', updateBattery);
      batt.addEventListener('chargingchange', updateBattery);
    }).catch(() => {});
  }

  function effectiveInterval() {
    return HEARTBEAT_BASE * backoffMult * getSaveDataFactor() * batteryFactor;
  }

  // Helpers: check if element is visible in viewport
  function elementInViewport(el) {
    try {
      const r = el.getBoundingClientRect();
      return r.bottom >= 0 && r.right >= 0 && r.top <= (window.innerHeight || document.documentElement.clientHeight) && r.left <= (window.innerWidth || document.documentElement.clientWidth);
    } catch (e) {
      return false;
    }
  }

  // Capture only text nodes whose parent element intersects viewport and is visible
  function visibleViewportText() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    let node;
    const chunks = [];
    while ((node = walker.nextNode())) {
      const txt = (node.nodeValue || '').trim();
      if (!txt) continue;
      const parent = node.parentElement;
      if (!parent) continue;
      const style = window.getComputedStyle(parent);
      if (style && (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity || '1') === 0)) continue;
      if (!elementInViewport(parent)) continue;
      chunks.push(txt);
    }
    return chunks.join(' ');
  }

  // Post captured text to backend (uses cachedEndpoint to avoid storage callbacks)
  function postCapture(text) {
    if (!text) return;
    const endpoint = cachedEndpoint || 'http://127.0.0.1:8000/analyze';
    try {
      // Ask background for user email + token non-interactively
      chrome.runtime.sendMessage({ type: 'GET_USER_EMAIL', interactive: false }, async (resp) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          console.debug('GET_USER_EMAIL runtime error:', chrome.runtime.lastError.message || String(chrome.runtime.lastError));
        }
        const headers = { 'Content-Type': 'application/json' };
        let body = { messages: [{ sender: 'page', text }], source: location.href };
        if (resp && resp.token) headers['Authorization'] = 'Bearer ' + resp.token;
        if (resp && resp.email) body.user_id = resp.email;
        try {
          const r = await fetch(endpoint, { method: 'POST', headers, body: JSON.stringify(body), keepalive: true });
          if (!r.ok) {
            console.warn('analyze endpoint returned', r.status);
            backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
          } else {
            backoffMult = 1;
          }
        } catch (err) {
          console.warn('analyze POST failed', err && (err.message || String(err)));
          backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
        } finally {
          scheduleHeartbeat();
        }
      });
    } catch (e) {
      console.warn('postCapture error', e && (e.message || String(e)));
      backoffMult = Math.min(MAX_BACKOFF_MULT, backoffMult * 2);
      scheduleHeartbeat();
    }
  }

  function captureNow() {
    if (document.visibilityState !== 'visible') return;
    const text = visibleViewportText();
    const hash = (text || '').slice(0, DEDUP_CHARS);
    if (!hash) return;
    if (hash === lastCapturedHash) return;
    lastCapturedHash = hash;
    try {
      chrome.storage.local.set({ lastCapturedText: text, capturedAt: new Date().toISOString() });
      try { chrome.runtime.sendMessage({ type: 'PAGE_TEXT_CAPTURED', text }); } catch (e) { /* ignore */ }
    } catch (e) {
      console.warn('storage set failed', e);
    }
    postCapture(text);
  }

  // Activity handling: pause sampling while user active, resume after IDLE_CAPTURE_DELAY
  function onUserActivity() {
    isUserActive = true;
    if (activityTimer) clearTimeout(activityTimer);
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
    activityTimer = setTimeout(() => {
      isUserActive = false;
      captureNow();
      scheduleHeartbeat();
    }, IDLE_CAPTURE_DELAY);
  }

  // Heartbeat captures while user is idle
  function scheduleHeartbeat() {
    if (heartbeatTimer) clearTimeout(heartbeatTimer);
    heartbeatTimer = setTimeout(() => {
      if (!isUserActive && document.visibilityState === 'visible') captureNow();
      scheduleHeartbeat();
    }, Math.max(5000, effectiveInterval()));
  }

  // Wire activity listeners
  ['scroll', 'keydown', 'mousemove', 'touchstart'].forEach(evt => {
    window.addEventListener(evt, onUserActivity, { passive: true });
  });

  // Visibility change should pause/resume
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') {
      if (heartbeatTimer) clearTimeout(heartbeatTimer);
    } else {
      captureNow();
      scheduleHeartbeat();
    }
  });

  // Initial schedule
  scheduleHeartbeat();
  // initial passive capture attempt in case user is idle on load
  setTimeout(() => { if (!isUserActive) captureNow(); }, 1000);
})();