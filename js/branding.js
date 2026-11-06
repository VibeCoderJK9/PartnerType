/* ============================================================
   PARTNERTYPE — THE PRODUCER'S BRAND
   ============================================================

   Every screen of a producer's system -- the login page a partner opens at
   acme.partnertype.com first of all -- carries the producer's name and logo
   in place of PartnerType's. They come from the open
   /api/branding (api/branding.py), so this works before anybody signs in.

   What it changes:
   - every <span data-i18n="partner_center"> (the name beside the logo, on the
     sign-in pages and in the sidebar) takes the producer's name, and loses the
     data-i18n attribute: a language switch would otherwise write "PartnerType"
     back over it. A company name reads the same in both languages.
   - every .brand-logo image takes the producer's logo, in both themes.
   - the "partner programme terms" words on the partner sign-up form
     link to the producer's own terms when he gave an address, and stay plain
     text when he did not -- never a link to nowhere.

   The last answer is remembered in localStorage and applied at once on the
   next page, so moving around the app does not flash PartnerType's mark
   first. Failing silently is correct: the PartnerType defaults stay.
   ============================================================ */
(function (global) {
  'use strict';

  var STORE = 'ph_branding';

  function remembered() {
    try {
      return JSON.parse(global.localStorage.getItem(STORE) || 'null');
    } catch (error) {
      return null;
    }
  }

  function remember(info) {
    try {
      global.localStorage.setItem(STORE, JSON.stringify(info));
    } catch (error) {
      // Private mode: the next page asks again.
    }
  }

  function apply(info) {
    if (!info) return;
    if (info.company_name) {
      document.querySelectorAll('span[data-i18n="partner_center"], span[data-brand-name]').forEach(function (span) {
        span.removeAttribute('data-i18n');
        span.setAttribute('data-brand-name', '');
        span.textContent = info.company_name;
      });
    }
    if (info.logo_version) {
      var src = '/api/branding/logo?v=' + encodeURIComponent(info.logo_version);
      document.querySelectorAll('img.brand-logo').forEach(function (img) {
        img.src = src;
        img.alt = info.company_name || '';
      });
    }
  }

  // Only an absolute http(s) address may become the terms link. Anything else
  // (javascript:, data:, a relative path) leaves the words as plain text.
  function safeTermsUrl(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim())) return null;
    try {
      var url = new URL(value.trim());
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
    } catch (error) {
      return null;
    }
  }

  function applyTerms(info) {
    var termsUrl = safeTermsUrl(info && info.partner_terms_url);
    document.querySelectorAll('a[data-partner-terms]').forEach(function (link) {
      if (termsUrl) {
        link.href = termsUrl;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      } else {
        link.removeAttribute('href');
      }
    });
  }

  apply(remembered());

  global.fetch('/api/branding', { credentials: 'same-origin' })
    .then(function (response) { return response.ok ? response.json() : null; })
    .then(function (info) {
      if (!info) return;
      remember(info);
      apply(info);
      applyTerms(info);
    })
    .catch(function () {});

  global.Branding = { apply: apply, remember: remember };
})(window);
