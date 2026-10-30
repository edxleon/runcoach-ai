// The Garmin sign-in dialog: the same sign-in as `runcoach login`, from the
// page. Two steps - the account (e-mail, password), then the code Garmin
// sends when the account uses two-step verification. The notice is the
// terminal's, from the server (`auth.NOTICE_POINTS`), so the two cannot
// promise different things.
// Pure: step in, HTML out, so web-tests render it without a page. app.js loads
// it on the first click (not with the page: one module fewer on every start),
// opens the <dialog>, posts the form and swaps the step.
import { esc } from "./ui.js";

export function signinHtml(step, notice) {
  const head = `<div class="signin-head">
      <h2 class="signin-title" id="signin-title">Sign in to Garmin</h2>
      <button type="button" class="linkish" data-signin-cancel>Cancel</button>
    </div>`;
  if (step === "code") {
    return `${head}
    <form class="signin-form" data-signin-form="code" autocomplete="on">
      <p class="note">Garmin sent you a code - by e-mail or in the Garmin Connect app,
        depending on your account. It is used once, here, and not kept.</p>
      <fieldset>
        <label class="signin-label">Code
          <input class="field" name="code" inputmode="numeric" autocomplete="one-time-code"
                 pattern="[0-9 ]{4,12}" maxlength="12" required></label>
        <div class="form-error" data-signin-error role="alert"></div>
        <div class="btn-row form-actions">
          <button type="submit" class="btn btn-primary" data-label="Confirm">Confirm</button>
        </div>
      </fieldset>
    </form>`;
  }
  const points = (notice || []).map(p => `<li>${esc(p)}</li>`).join("");
  return `${head}
    <form class="signin-form" data-signin-form="account" autocomplete="on">
      ${points ? `<ul class="signin-notice">${points}</ul>` : ""}
      <fieldset>
        <label class="signin-label">Garmin e-mail
          <input class="field" type="email" name="email" autocomplete="username"
                 maxlength="254" required></label>
        <label class="signin-label">Password
          <input class="field" type="password" name="password" autocomplete="current-password"
                 maxlength="256" required></label>
        <div class="form-error" data-signin-error role="alert"></div>
        <div class="btn-row form-actions">
          <button type="submit" class="btn btn-primary" data-label="Sign in">Sign in</button>
        </div>
      </fieldset>
    </form>`;
}
