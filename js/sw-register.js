/* ============================================================
   PARTNERTYPE — SERVICE WORKER REGISTRATION
   ============================================================

   Registers sw.js once the page has finished loading, so the registration
   never competes with the login screen for the network.

   The rejection is swallowed on purpose. A service worker is unavailable in
   private-browsing windows, over plain http on a non-localhost host, and
   wherever site data is blocked — none of which is a reason to put an error in
   front of somebody trying to sign in. The application works without it.

   Kept in a file so no page carries an inline <script> and `script-src`
   does not have to allow them.
   ============================================================ */
(function registerServiceWorker() {
  'use strict';
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', function register() {
    navigator.serviceWorker.register('/sw.js').catch(function ignore() {});
  });
})();
