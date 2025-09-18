// background.js - listens for messages and can coordinate captures
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === 'PAGE_TEXT_CAPTURED') {
    // simple example: log and keep a last-seen mapping per tab
    const tabId = sender.tab ? sender.tab.id : 'unknown';
    chrome.storage.local.set({ ['text_tab_' + tabId]: { text: msg.text, capturedAt: new Date().toISOString() } });
  }
});
