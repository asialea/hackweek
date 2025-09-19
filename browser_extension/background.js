// background.js - service worker for MV3 to handle OAuth login via chrome.identity.launchWebAuthFlow
// Replace AUTH_CONFIG values with your provider/backend endpoints.
const AUTH_CONFIG = {
  // If your provider returns an access_token in the redirect fragment, the extension will store it.
  // If your provider returns a code, set tokenExchangeUrl to a backend endpoint that will exchange code+verifier for a JWT
  clientId: '991999948488-qjk1ha86fvfka97ud686e6rfakr6g45c.apps.googleusercontent.com',
  authUrl: 'https://accounts.google.com/o/oauth2/v2/auth',
  scope: 'openid profile email',
  redirectUri: 'https://gdhpboaofomkjkgabhobogcnebgbfoha.chromiumapp.org/',
  responseType: 'token', // or "id_token token" if you want both
};

function randomString(len = 43) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
  let s = '';
  const arr = new Uint8Array(len);
  crypto.getRandomValues(arr);
  for (let i = 0; i < len; i++) s += chars[arr[i] % chars.length];
  return s;
}

async function sha256Base64Url(str) {
  const encoder = new TextEncoder();
  const data = encoder.encode(str);
  const hash = await crypto.subtle.digest('SHA-256', data);
  const bytes = new Uint8Array(hash);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  let base64 = btoa(binary);
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function startAuthFlow() {
  const redirectUri = chrome.identity.getRedirectURL('provider_cb');
  const state = randomString(16);
  const codeVerifier = randomString(64);
  const codeChallenge = await sha256Base64Url(codeVerifier);

  const authUrl = `${AUTH_CONFIG.authUrl}?response_type=code%20token&client_id=${encodeURIComponent(AUTH_CONFIG.clientId)}&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(AUTH_CONFIG.scope)}&state=${encodeURIComponent(state)}&code_challenge=${encodeURIComponent(codeChallenge)}&code_challenge_method=S256`;

  await chrome.storage.local.set({ _auth_state: state, _code_verifier: codeVerifier });

  return new Promise((resolve, reject) => {
    chrome.identity.launchWebAuthFlow({ url: authUrl, interactive: true }, async (redirectUrl) => {
      if (chrome.runtime.lastError) return reject(chrome.runtime.lastError);
      if (!redirectUrl) return reject(new Error('No redirect URL'));

      try {
        // Redirect URL may contain fragment (#access_token=...) or query (?code=...)
        const u = new URL(redirectUrl);
        // Try fragment first
        const frag = redirectUrl.split('#')[1] || '';
        const fragParams = new URLSearchParams(frag);
        const accessTokenFromFrag = fragParams.get('access_token');
        const tokenType = fragParams.get('token_type');
        const returnedState = fragParams.get('state') || u.searchParams.get('state');

        const stored = await chrome.storage.local.get(['_auth_state','_code_verifier']);
        if (!stored._auth_state || stored._auth_state !== returnedState) {
          // state mismatch is a warning but still try to continue
          console.warn('state mismatch', stored._auth_state, returnedState);
        }

        if (accessTokenFromFrag) {
          // directly store access token
          const expiresIn = fragParams.get('expires_in');
          const expiresAt = expiresIn ? Date.now() + parseInt(expiresIn,10) * 1000 : null;
          await chrome.storage.local.set({ access_token: accessTokenFromFrag, access_token_expires_at: expiresAt });
          resolve({ access_token: accessTokenFromFrag, expires_at: expiresAt });
          return;
        }

        // If provider returned a code in query string, exchange with backend
        const code = u.searchParams.get('code');
        if (code) {
          const codeVerifier = stored._code_verifier;
          if (!AUTH_CONFIG.tokenExchangeUrl) return reject(new Error('No tokenExchangeUrl set for code exchange'));
          try {
            const resp = await fetch(AUTH_CONFIG.tokenExchangeUrl, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ code, code_verifier: codeVerifier, redirect_uri: redirectUri }),
            });
            if (!resp.ok) throw new Error('Token exchange failed: ' + resp.status);
            const data = await resp.json();
            // Expect backend to return { access_token, expires_in }
            const expiresAt = data.expires_in ? Date.now() + (data.expires_in * 1000) : null;
            await chrome.storage.local.set({ access_token: data.access_token, access_token_expires_at: expiresAt });
            resolve({ access_token: data.access_token, expires_at: expiresAt });
            return;
          } catch (err) {
            return reject(err);
          }
        }

        reject(new Error('No token or code in redirect'));
      } catch (err) {
        reject(err);
      }
    });
  });
}

async function getToken({ interactive = false } = {}) {
  const stored = await chrome.storage.local.get(['access_token','access_token_expires_at']);
  const now = Date.now();
  if (stored.access_token && stored.access_token_expires_at && stored.access_token_expires_at > now + 30000) {
    return stored.access_token;
  }
  // If token missing or expired: only start interactive auth when explicitly requested
  if (!interactive) {
    return null;
  }
  try {
    const res = await startAuthFlow();
    return res.access_token;
  } catch (e) {
    console.error('getToken failed', e && (e.message || JSON.stringify(e)));
    return null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return;
  if (message.type === 'START_LOGIN') {
    startAuthFlow().then(result => sendResponse({ ok: true, result })).catch(err => sendResponse({ ok: false, error: (err && (err.message || String(err))) }));
    return true;
  }
  if (message.type === 'GET_ACCESS_TOKEN') {
    // non-interactive by default; callers can pass { interactive: true }
    const interactive = !!message.interactive;
    getToken({ interactive }).then(token => {
      if (token) sendResponse({ token });
      else sendResponse({ error: 'no_token' });
    }).catch(err => sendResponse({ error: (err && (err.message || String(err))) }));
    return true;
  }
  // Return authenticated user's email (best-effort) and token if available.
  if (message.type === 'GET_USER_EMAIL') {
    const interactive = !!message.interactive;
    getToken({ interactive }).then(async (token) => {
      if (!token) {
        sendResponse({ error: 'no_token' });
        return;
      }
      try {
        // Use OpenID Connect userinfo endpoint to obtain email
        const resp = await fetch('https://openidconnect.googleapis.com/v1/userinfo', {
          headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!resp.ok) {
          sendResponse({ error: 'userinfo_failed', status: resp.status });
          return;
        }
        const profile = await resp.json();
        const email = profile && (profile.email || profile.preferred_username || profile.sub);
        // cache minimal info
        await chrome.storage.local.set({ user_email: email });
        sendResponse({ email, token, profile });
      } catch (err) {
        sendResponse({ error: (err && (err.message || String(err))) });
      }
    }).catch(err => sendResponse({ error: (err && (err.message || String(err))) }));
    return true;
  }
  if (message.type === 'LOGOUT') {
    (async () => {
      try {
        const stored = await chrome.storage.local.get(['access_token']);
        const token = stored && stored.access_token;
        if (token) {
          // Best-effort token revocation for Google (and OAuth2 providers that support token revocation)
          try {
            await fetch('https://oauth2.googleapis.com/revoke', {
              method: 'POST',
              headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
              body: 'token=' + encodeURIComponent(token),
            });
          } catch (revokeErr) {
            console.warn('token revocation failed', revokeErr && (revokeErr.message || revokeErr));
          }
        }
        await chrome.storage.local.remove(['access_token','access_token_expires_at','_auth_state','_code_verifier']);
        sendResponse({ ok: true });
      } catch (err) {
        sendResponse({ ok: false, error: (err && (err.message || String(err))) });
      }
    })();
    return true; // indicate we'll call sendResponse asynchronously
  }
});

// lightweight startup cleanup
chrome.runtime.onInstalled.addListener(() => {
  // no-op for now
});
// background.js - listens for messages and can coordinate captures
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === 'PAGE_TEXT_CAPTURED') {
    // simple example: log and keep a last-seen mapping per tab
    const tabId = sender.tab ? sender.tab.id : 'unknown';
    chrome.storage.local.set({ ['text_tab_' + tabId]: { text: msg.text, capturedAt: new Date().toISOString() } });
  }
});
