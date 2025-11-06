document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById('countdown');
  if (!el) return;
  const deadline = new Date(el.dataset.deadline).getTime();
  const tick = () => {
    const now = new Date().getTime();
    const diff = Math.max(0, deadline - now);
    const s = Math.floor(diff / 1000);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const mm = m % 60;
    const ss = s % 60;
    el.textContent = `${String(h).padStart(2,'0')}:${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
    if (diff <= 0) {
      const form = document.getElementById('quiz-form');
      if (form) form.submit();
      return;
    }
    setTimeout(tick, 500);
  };
  tick();
});