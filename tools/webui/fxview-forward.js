/* fxview-forward.js -- send /fxview to /effects, keeping the fragment.
 *
 * WHY THIS IS A FILE AND NOT AN INLINE <script>
 *   `tools/coviewer.py` attaches `script-src 'self'` to EVERY response, with
 *   no `'unsafe-inline'`, no nonce and no hash. An inline <script> body on a
 *   page this server sends is dead code, always, and it fails silently: the
 *   browser drops it and nothing on the page says so.
 *
 *   That is bugs_open.md #6, and this file exists because #6 happened TWICE.
 *   The first instance was the pop-out opt-out on the old /fxview shell. The
 *   fix for it retired that shell and left this forward behind as an inline
 *   <script> -- the same defect, in the same file, introduced by the commit
 *   that closed it. Measured 2026-09-10 in headless Chrome, three seconds
 *   after loading /fxview:
 *
 *       { href: ".../fxview", path: "/fxview", h1: "The Effects Viewer moved" }
 *       FORWARD FIRED: False
 *
 *   The page's own fallback line -- "You should have been sent there
 *   automatically. If you are reading this, script did not run" -- was what
 *   every single visitor saw. The page shipped its own diagnosis and was
 *   always in the failure branch.
 *
 * WHY SCRIPT AT ALL, RATHER THAN <meta http-equiv="refresh">
 *   A meta refresh drops `#kind=3d&name=Whatever`, and links landing on the
 *   right EFFECT is this page's whole reason for existing. That argument was
 *   right when it was written; it just needed the code somewhere the CSP
 *   permits. Both halves are available and this is the one that keeps them.
 *
 * WHY location.replace AND NOT location.href
 *   A redirect that pushes a history entry makes Back bounce off this page
 *   and straight forward again, which reads as a broken Back button.
 *
 * NOTHING BELOW THE FOLD DEPENDS ON THIS RUNNING. The body of fxview.html is
 * a working link to the same destination, so a browser with script disabled
 * still gets there in one click rather than onto an empty page.
 */
(function () {
  'use strict';
  var frag = window.location.hash || '';
  window.location.replace('/effects' + frag);
}());
