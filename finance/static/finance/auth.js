/* Login / sign-up: show or hide the password in each field that has a toggle button. */
document.querySelectorAll('.password-toggle').forEach((btn) => {
  const input = document.getElementById(btn.dataset.target);
  btn.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show));
    btn.setAttribute('aria-label', show ? 'Esconder palavra-passe' : 'Mostrar palavra-passe');
    btn.querySelector('i').className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
    input.focus();
  });
});
