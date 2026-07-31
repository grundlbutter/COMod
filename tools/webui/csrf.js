/* csrf.js -- attach this run's CSRF token to every same-origin POST.
 *
 * WHY A FETCH WRAPPER AND NOT AN ARGUMENT AT EVERY CALL SITE
 * ---------------------------------------------------------
 * The UI issues POSTs from five separate files (app.js, builder.js,
 * companion.js, mapedit.js, play.js) at a couple of dozen call sites. Threading
 * a header through all of them is the version of this change that misses one,
 * and the one it misses is a route that silently stops working -- or worse,
 * gets an exemption added to make it work again.
 *
 * So the token is attached once, here, by wrapping `fetch`. Every existing POST
 * in the UI keeps working with no edit at all, and a POST added later is
 * covered before it is written.
 *
 * WHERE THE TOKEN COMES FROM
 * --------------------------
 * `coviewer.Handler._inject_csrf` puts `<meta name="co-csrf">` into the page at
 * serve time. It is deliberately not in any file on disk: it is new on every
 * run of the server, so a token that leaked cannot outlive the process it
 * authorised.
 *
 * A page on another origin cannot read this value. It can *make* the browser
 * POST to our port -- that is what CSRF is -- but the same-origin policy makes
 * our HTML opaque to it, so it cannot learn the token, and the POST is refused.
 *
 * SAME-ORIGIN ONLY, ON PURPOSE
 * ----------------------------
 * The wrapper attaches the header only to requests going back to this origin.
 * If some future page fetches a third-party URL, this must not hand our token
 * to it. Relative URLs -- which is what the whole UI uses -- are same-origin by
 * definition.
 */
(function () {
  'use strict';

  var meta = document.querySelector('meta[name="co-csrf"]');
  var token = meta ? meta.getAttribute('content') : '';
  if (!token) return;                     // server did not inject one

  // Exposed for anything that needs to send a request by other means (an
  // XMLHttpRequest, a form, a diagnostic in the console).
  window.CO_CSRF = token;

  var sameOrigin = function (url) {
    try {
      return new URL(url, location.href).origin === location.origin;
    } catch (e) {
      return false;                       // unparseable: do not attach
    }
  };

  var native = window.fetch;
  if (typeof native !== 'function') return;

  window.fetch = function (input, init) {
    var url = (typeof input === 'string') ? input
            : (input && input.url) ? input.url : '';
    var method = ((init && init.method)
               || (input && input.method) || 'GET').toUpperCase();

    if (method === 'GET' || method === 'HEAD' || !sameOrigin(url)) {
      return native.call(this, input, init);
    }

    // Headers may arrive as a Headers instance, a plain object, or an array of
    // pairs. Normalising through Headers handles all three without caring.
    var opts = Object.assign({}, init || {});
    var h = new Headers((init && init.headers)
                     || (input && input.headers) || undefined);
    if (!h.has('X-CO-Token')) h.set('X-CO-Token', token);
    opts.headers = h;
    return native.call(this, input, opts);
  };
})();
