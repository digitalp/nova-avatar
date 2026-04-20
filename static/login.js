'use strict';

async function checkSetupRequired() {
  try {
    const r = await fetch('/admin/setup-required');
    const d = await r.json();
    if (d.required) {
      document.getElementById('login-form').style.display = 'none';
      document.getElementById('setup-form').style.display = 'block';
    }
  } catch (_) {}
}

checkSetupRequired();

// Focus username on load
window.addEventListener('load', () => {
  const visible = document.getElementById('setup-form').style.display !== 'none'
    ? 'setup-username' : 'username';
  document.getElementById(visible)?.focus();
});

document.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });

async function submit() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const errEl    = document.getElementById('error-msg');
  const btn      = document.getElementById('submit-btn');

  errEl.textContent = '';
  if (!username || !password) { errEl.textContent = 'Please enter username and password.'; return; }

  btn.disabled = true; btn.textContent = 'Signing in…';
  try {
    const r = await fetch('/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (r.ok) {
      window.location.href = '/admin';
    } else {
      const d = await r.json().catch(() => ({}));
      errEl.textContent = d.detail || 'Invalid credentials.';
    }
  } catch (_) {
    errEl.textContent = 'Cannot reach server.';
  } finally {
    btn.disabled = false; btn.textContent = 'Sign in';
  }
}

async function setup() {
  const username = document.getElementById('setup-username').value.trim();
  const password = document.getElementById('setup-password').value;
  const confirm  = document.getElementById('setup-confirm').value;
  const errEl    = document.getElementById('setup-error');
  const btn      = document.getElementById('setup-btn');

  errEl.textContent = '';
  if (!username)               { errEl.textContent = 'Username is required.'; return; }
  if (password.length < 8)     { errEl.textContent = 'Password must be at least 8 characters.'; return; }
  if (password !== confirm)    { errEl.textContent = 'Passwords do not match.'; return; }

  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const r = await fetch('/admin/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (r.ok) {
      window.location.href = '/admin/login';
    } else {
      const d = await r.json().catch(() => ({}));
      errEl.textContent = d.detail || 'Setup failed.';
    }
  } catch (_) {
    errEl.textContent = 'Cannot reach server.';
  } finally {
    btn.disabled = false; btn.textContent = 'Create Account';
  }
}

document.getElementById('submit-btn').addEventListener('click', submit);
document.getElementById('setup-btn').addEventListener('click', setup);
