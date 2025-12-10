const loginForm = document.getElementById('loginForm');
const registerForm = document.getElementById('registerForm');
const loginFeedback = document.getElementById('loginFeedback');
const registerFeedback = document.getElementById('registerFeedback');
const registrationDisabled = document.getElementById('registrationDisabled');
const registerButton = document.getElementById('registerButton');

async function fetchConfig() {
  try {
    const resp = await fetch('/api/auth/config');
    const data = await resp.json();
    if (!data.registration_enabled) {
      registrationDisabled.hidden = false;
      registerButton.disabled = true;
      registerForm.querySelectorAll('input').forEach((el) => (el.disabled = true));
    }
  } catch (err) {
    console.error('Unable to load auth config', err);
  }
}

function setFeedback(el, message, isError = true) {
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.toggle('success', !isError);
}

function validateForm(form) {
  return form.reportValidity();
}

async function handleLogin(event) {
  event.preventDefault();
  if (!validateForm(loginForm)) return;
  const payload = {
    username: loginForm.loginUsername.value.trim(),
    password: loginForm.loginPassword.value,
  };
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setFeedback(loginFeedback, data.detail || 'Unable to login');
      return;
    }
    setFeedback(loginFeedback, 'Login successful. Redirecting...', false);
    window.location.href = '/';
  } catch (err) {
    console.error('Login failed', err);
    setFeedback(loginFeedback, 'Unexpected error during login');
  }
}

async function handleRegister(event) {
  event.preventDefault();
  if (!validateForm(registerForm)) return;
  if (registerForm.registerPassword.value !== registerForm.registerConfirm.value) {
    setFeedback(registerFeedback, 'Passwords must match');
    return;
  }
  const payload = {
    username: registerForm.registerUsername.value.trim(),
    password: registerForm.registerPassword.value,
    confirm_password: registerForm.registerConfirm.value,
  };
  try {
    const resp = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setFeedback(registerFeedback, data.detail || 'Registration failed');
      return;
    }
    setFeedback(registerFeedback, 'Account created. You can now sign in.', false);
    registerForm.reset();
  } catch (err) {
    console.error('Registration failed', err);
    setFeedback(registerFeedback, 'Unexpected error during registration');
  }
}

loginForm?.addEventListener('submit', handleLogin);
registerForm?.addEventListener('submit', handleRegister);

void fetchConfig();
