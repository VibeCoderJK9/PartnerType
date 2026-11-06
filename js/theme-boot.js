/* ============================================================
   PARTNERTYPE — THEME BOOT
   ============================================================

   The chosen theme, applied before the first paint.

   This has to run BEFORE the browser draws anything, or a dark-theme user sees
   a white page flash and then go dark on every navigation. That is why the tag
   that loads this file sits in <head> with no `defer` and no `async`: it is the
   one script on these pages that is deliberately render-blocking. It is four
   lines and served from the same origin, so the cost of that is a parse, not a
   round trip.

   A file rather than an inline <script> in every page, so the content-security
   policy never needs `script-src 'unsafe-inline'`, which cannot tell this line
   apart from one an attacker managed to write into a page.

   Failing silently is correct here: private-browsing modes and blocked site
   data make localStorage throw on read, and a wrong theme is a far smaller
   problem than a page that stops loading.
   ============================================================ */
(function bootTheme() {
  'use strict';
  var theme = 'light';
  try {
    theme = localStorage.getItem('ph_theme') || 'light';
  } catch (error) {
    theme = 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
