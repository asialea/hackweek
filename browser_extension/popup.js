// popup.js - read lastCapturedText from storage and display
document.addEventListener('DOMContentLoaded', () => {
  const textEl = document.getElementById('text');
  const metaEl = document.getElementById('meta');
  const refresh = document.getElementById('refresh');
  const copy = document.getElementById('copy');

  function load() {
    chrome.storage.local.get(['lastCapturedText', 'capturedAt'], (res) => {
      textEl.value = res.lastCapturedText || '';
      metaEl.textContent = res.capturedAt ? 'Captured: ' + res.capturedAt : '';
    });
  }

  refresh.addEventListener('click', load);
  copy.addEventListener('click', () => {
    textEl.select();
    document.execCommand('copy');
  });

  load();
  // load and save analysis endpoint
  const endpointInput = document.getElementById('endpoint');
  const saveBtn = document.getElementById('save-endpoint');
  chrome.storage.local.get(['analyzeEndpoint'], (res) => {
    endpointInput.value = res.analyzeEndpoint || '';
  });
  saveBtn.addEventListener('click', () => {
    const val = endpointInput.value.trim();
    chrome.storage.local.set({ analyzeEndpoint: val }, () => {
      alert('Endpoint saved');
    });
  });
});
