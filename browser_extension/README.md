Screen Text Reader — minimal Chrome extension

How it works
- A `content.js` content script runs on configured hosts and extracts visible text.
- Extracted text is stored in `chrome.storage.local` and can be viewed via the popup.

Files created
- `manifest.json` — extension manifest (MV3)
- `content.js` — content script to extract visible text
- `background.js` — background service worker to receive messages
- `popup.html`, `popup.js` — UI for login pro,pt

Load locally
1. Open chrome://extensions
2. Enable Developer mode
3. "Load unpacked" and choose this folder `browser_extension/`

Notes & privacy
- This prototype stores page text locally in the extension storage. For any sensitive use, add explicit user consent and implement secure handling and opt-out flows.
- Update `manifest.json` host matches with the exact domains you want to capture.
