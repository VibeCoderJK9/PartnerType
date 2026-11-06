/* ============================================================
   PartnerType referral snippet, for the producer's own shop
   ============================================================

   Paste it into the shop's pages (the administrator copies it from
   Settings › Integrations › Affiliate). It never contacts PartnerType.

   A visitor arrives from a partner's link as
   https://shop.example.com/?pt_ref=<code>&pt_click=<click id>.
   Nothing is stored until the shop calls PartnerTypeRef.consent(), after the
   visitor has agreed to marketing cookies; then one first-party cookie,
   pt_ref, keeps the click id (or the code) for the configured number of
   days. A later visit through another partner's link replaces it: the last
   click wins. At checkout the shop reads PartnerTypeRef.get() and sends it as
   "ref" in the signed webhook.
   ============================================================ */
(function (window, document) {
  "use strict";

  var NAME = "pt_ref";
  var DEFAULT_DAYS = 30;
  var VALUE = /^[A-Za-z0-9_-]{1,64}$/;

  function days() {
    var configured = Math.floor(Number(window.PartnerTypeRefDays));
    return configured >= 1 && configured <= 365 ? configured : DEFAULT_DAYS;
  }

  function fromAddress() {
    var params = new URLSearchParams(window.location.search);
    var click = params.get("pt_click");
    if (click && VALUE.test(click)) return click;
    var code = params.get("pt_ref");
    return code && VALUE.test(code) ? code : null;
  }

  var arrived = fromAddress();

  function write(value, maxAge) {
    var secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = NAME + "=" + value + "; Max-Age=" + maxAge +
      "; Path=/; SameSite=Lax" + secure;
  }

  function read() {
    var match = document.cookie.match(/(?:^|;\s*)pt_ref=([A-Za-z0-9_-]{1,64})(?:;|$)/);
    return match ? match[1] : null;
  }

  window.PartnerTypeRef = {
    /** Call once the visitor has agreed to marketing cookies. */
    consent: function () {
      if (arrived) write(arrived, days() * 86400);
    },
    /** The referral to send as "ref" at checkout, or null. */
    get: function () {
      return read();
    },
    /** Call when the visitor withdraws consent. */
    clear: function () {
      write("", 0);
    },
  };
})(window, document);
