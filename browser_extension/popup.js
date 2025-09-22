// Minimal popup script: check login status and offer Login/Logout
document.addEventListener('DOMContentLoaded', () => {
  const status = document.getElementById('status');
  function setStatus(s) { if (status) status.textContent = s; }

  // On open, check non-interactively whether we have a stored token/session.
  chrome.runtime.sendMessage({ type: 'GET_ACCESS_TOKEN', interactive: false }, (resp) => {
    if (chrome.runtime && chrome.runtime.lastError) {
      setStatus('Error checking login');
      return;
    }
    if (resp && resp.token) {
      // If background stored a user_email, GET_USER_EMAIL may return it; prefer displaying email
      chrome.runtime.sendMessage({ type: 'GET_USER_EMAIL', interactive: false }, (u) => {
        if (u && u.email) setStatus('Logged in as ' + u.email);
        else setStatus('Logged in');
      });
    } else {
      // Not logged in: because the popup open is a user gesture, start interactive auth
      setStatus('Not logged in — opening sign-in...');
      chrome.runtime.sendMessage({ type: 'GET_ACCESS_TOKEN', interactive: true }, (resp2) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          setStatus('Login failed');
          return;
        }
        if (resp2 && resp2.token) {
          chrome.runtime.sendMessage({ type: 'GET_USER_EMAIL', interactive: false }, (u) => {
            if (u && u.email) setStatus('Logged in as ' + u.email);
            else setStatus('Logged in');
          });
        } else setStatus('Login required');
      });
    }
  });

  const loginBtn = document.getElementById('login');
  const logoutBtn = document.getElementById('logout');

  if (loginBtn) loginBtn.addEventListener('click', () => {
    setStatus('Opening login...');
    // START_LOGIN will run an interactive auth flow in background.js
    chrome.runtime.sendMessage({ type: 'START_LOGIN' }, (resp) => {
      if (resp && resp.ok) setStatus('Logged in');
      else setStatus('Login cancelled or failed');
    });
  });

  if (logoutBtn) logoutBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'LOGOUT' }, (resp) => {
      setStatus('Logged out');
    });
  });
});
