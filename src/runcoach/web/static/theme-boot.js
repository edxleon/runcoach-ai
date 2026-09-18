/* Applies the stored theme BEFORE the first paint, so a dark-mode user never
   gets a white flash. It has to be a separate, render-blocking file rather than
   an inline <script>: the server's Content-Security-Policy is `script-src
   'self'`, which is what keeps a future stray innerHTML from becoming a script
   sink — and an inline script would be blocked by exactly that rule. The cost is
   one request from localhost.

   Kept in sync with theme.js, which owns the toggle; this only reads. */
try {
  const stored = localStorage.getItem("runcoach-theme");
  if (stored === "light" || stored === "dark") document.documentElement.dataset.theme = stored;
} catch (e) {
  /* private mode, blocked site data: the CSS media query still decides */
}
