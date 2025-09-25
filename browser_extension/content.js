// content.js - extracts visible text from the page and sends it to the backend
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
      // First try to get a stored backend-issued token (local JWT) non-interactively
      chrome.runtime.sendMessage({ type: 'GET_ACCESS_TOKEN', interactive: false }, async (tokResp) => {
        const headers = { 'Content-Type': 'application/json' };
        // The /analyze endpoint expects a JSON array of messages directly
        let body = [{ sender: 'page', text }];
        if (tokResp && tokResp.token) {
          headers['Authorization'] = 'Bearer ' + tokResp.token;
        }
        // Also try to get cached user_email if available (non-interactive)
        chrome.runtime.sendMessage({ type: 'GET_USER_EMAIL', interactive: false }, async (userResp) => {
          // Note: user_id is now derived from JWT on backend, so we don't need to send it
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