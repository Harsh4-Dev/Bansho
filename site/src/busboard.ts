import './styles/busboard.css';
import { startBeacon } from './beacon';

// Bus Board's own "day" skin: the same two tones, swapped, for phones in
// direct sun. Remembered per browser when storage is available.
const KEY = 'busboard.skin';
const root = document.documentElement;

function apply(skin: string | null): void {
  if (skin === 'day') root.setAttribute('data-skin', 'day');
  else root.removeAttribute('data-skin');
  const btn = document.getElementById('skin');
  if (btn) btn.textContent = skin === 'day' ? '[night]' : '[day]';
}

let saved: string | null = null;
try {
  saved = window.localStorage.getItem(KEY);
} catch {
  saved = null;
}
apply(saved);
document.getElementById('skin')?.addEventListener('click', () => {
  const next = root.getAttribute('data-skin') === 'day' ? 'night' : 'day';
  apply(next);
  try {
    window.localStorage.setItem(KEY, next);
  } catch {
    /* fine: skin lasts for this visit */
  }
});

startBeacon();
