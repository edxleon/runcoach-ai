"""The Garmin sign-in from the web page (`auth.WebLogin`, POST /api/garmin/login):
the same sign-in as `runspector login`, in two requests. What it must never do is
keep the password - on disk, in an answer, in the state - or take one from a
device whose connection to the page is plain HTTP across a network."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import garminconnect
import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from runspector import auth, paths
from runspector.store import Store
from runspector.web import server
from test_web import _serve, call

PASSWORD = "correct horse battery staple"


class FakeSignIn:
    """The library's two-step sign-in as the app uses it: `login()` answers
    `("needs_mfa", state)` for an account with a code, `resume_login` checks
    the code, `client.dump` writes the token."""

    mfa = True
    code = "123456"
    fail_with: Exception | None = None
    #: Raised AFTER Garmin took the code - the library's profile load or token
    #: check failing - with `token` saying whether the client still holds one.
    after_code: tuple[Exception, bool] | None = None
    made: list = []

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.username, self.password, self.return_on_mfa = email, password, return_on_mfa
        self.signed_in = False
        # The library's own bookkeeping, as `WebLogin` reads it.
        self.client = SimpleNamespace(dump=self._dump, _mfa_pending=False, is_authenticated=False)
        FakeSignIn.made.append(self)

    def login(self, tokenstore=None):
        assert self.return_on_mfa, "the web sign-in must not block on a prompt"
        if FakeSignIn.fail_with:
            raise FakeSignIn.fail_with
        if FakeSignIn.mfa:
            self.client._mfa_pending = True
            return "needs_mfa", {"cookies": "secret-session"}
        self.signed_in = True
        return None, None

    def resume_login(self, state, code):
        assert state == {"cookies": "secret-session"}
        if code != FakeSignIn.code:
            raise GarminConnectAuthenticationError("Widget MFA failed: bad code")
        if FakeSignIn.after_code:
            # The real library may still report the sign-in as pending here
            # (round-2 finding: the token exchange runs inside the code step),
            # so the app must not read anything into that flag.
            exc, token = FakeSignIn.after_code
            self.signed_in = self.client.is_authenticated = token
            raise exc
        self.client._mfa_pending = False
        self.signed_in = self.client.is_authenticated = True
        return None, None

    def _dump(self, store):
        assert self.signed_in, "a token was saved before Garmin said yes"
        with open(f"{store}/garmin_tokens.json", "w", encoding="utf-8") as fh:
            fh.write('{"di_token": "tok"}')


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(garminconnect, "Garmin", FakeSignIn)
    monkeypatch.setattr(FakeSignIn, "mfa", True)
    monkeypatch.setattr(FakeSignIn, "fail_with", None)
    monkeypatch.setattr(FakeSignIn, "after_code", None)
    monkeypatch.setattr(FakeSignIn, "made", [])
    return FakeSignIn


def _everything_on_disk() -> str:
    return "".join(f.read_text(encoding="utf-8", errors="replace")
                   for f in paths.home().rglob("*") if f.is_file())


# ── auth.WebLogin ────────────────────────────────────────────────────────────

def test_without_a_code_the_token_is_saved_and_the_password_is_not(fake):
    fake.mfa = False
    assert auth.WebLogin().start("a@example.org", PASSWORD) == {"state": "signed_in"}
    assert paths.garmin_session_present()
    assert PASSWORD not in _everything_on_disk() and "a@example.org" not in _everything_on_disk()
    assert fake.made[0].password is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_saved_token_is_readable_by_its_owner_only(fake):
    fake.mfa = False
    auth.WebLogin().start("a@example.org", PASSWORD)
    store = paths.garmin_dir()
    assert store.stat().st_mode & 0o777 == 0o700
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in store.iterdir())


def test_a_code_is_asked_for_and_a_wrong_one_can_be_corrected(fake):
    login = auth.WebLogin()
    assert login.start("a@example.org", PASSWORD) == {"state": "code"}
    assert login.waiting_for_code() and not paths.garmin_session_present()
    # The pending sign-in does not hold on to the password while it waits.
    assert fake.made[0].password is None

    wrong = login.finish("000000")
    assert "did not accept that code" in wrong["error"] and login.waiting_for_code()
    assert not paths.garmin_session_present()

    assert login.finish("123456") == {"state": "signed_in"}
    assert not login.waiting_for_code() and paths.garmin_session_present()


def test_only_a_new_sign_in_counts_as_one(fake):
    """Fresh reader: a page following a late answer took "there is a session"
    for success - but a re-sign-in starts with the old, expired token on disk.
    The count of sign-ins that went through is what moves."""
    login = auth.WebLogin()
    assert login.signins == 0
    login.start("a@example.org", PASSWORD)
    login.finish("000000")
    assert login.signins == 0
    login.finish("123456")
    assert login.signins == 1


def test_a_sign_in_waits_for_its_code_only_so_long(fake):
    now = [1000.0]
    login = auth.WebLogin(clock=lambda: now[0])
    login.start("a@example.org", PASSWORD)
    now[0] += auth.MFA_WAIT_S + 1
    assert not login.waiting_for_code()
    out = login.finish("123456")
    assert out["restart"] is True and "expired" in out["error"]
    assert not paths.garmin_session_present()


def test_a_code_garmin_took_is_never_called_wrong(fake):
    """Review finding: Garmin accepts the code, then the library's profile load
    fails - and the page said "did not accept that code", kept asking for it,
    and a re-entered code replayed a used one for ten minutes. The token the
    client already held is the sign-in: it is kept."""
    fake.after_code = (GarminConnectAuthenticationError("Failed to retrieve social profile"), True)
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    assert login.finish("123456") == {"state": "signed_in"}
    assert not login.waiting_for_code() and paths.garmin_session_present()


def test_a_failed_code_offers_a_new_one_and_three_end_the_sign_in(fake):
    """Round 2: whether Garmin took a code before something failed cannot be
    read reliably from the library - the token exchange runs inside the code
    step. So the app never claims to know: every failed code offers a fresh
    start, and after three the sign-in ends, so nobody loops on a spent code."""
    fake.after_code = (GarminConnectAuthenticationError("JWT_WEB cookie not set"), False)
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    for _ in range(auth.MFA_TRIES - 1):
        out = login.finish("123456")
        assert "start again" in out["error"] and "restart" not in out
        assert login.waiting_for_code()
    out = login.finish("123456")
    assert out["restart"] is True and not login.waiting_for_code()
    assert not paths.garmin_session_present()


def test_an_expired_sign_in_lets_go_of_garmins_session(fake):
    """Expired means gone, not merely reported as gone: the pending sign-in
    holds Garmin's session cookies."""
    now = [1000.0]
    login = auth.WebLogin(clock=lambda: now[0])
    login.start("a@example.org", PASSWORD)
    now[0] += auth.MFA_WAIT_S + 1
    assert not login.waiting_for_code() and login._pending is None


def test_a_code_that_could_not_be_checked_is_not_flatly_called_wrong(fake):
    """Security review: the library raises the same AuthenticationError for a
    wrong code and for a code it could not get checked (offline, Cloudflare),
    and keeps the sign-in open for both. The sentence may not claim more than
    that."""
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    out = login.finish("000000")
    assert "could not check it" in out["error"] and login.waiting_for_code()


def test_the_sign_in_does_not_depend_on_the_librarys_private_state(fake):
    """An older or newer garminconnect without `client.client` (0.2.x keeps
    `garth`) must still get a sentence, not an AttributeError."""
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    del fake.made[0].client
    out = login.finish("000000")
    assert "did not accept that code" in out["error"]


def test_a_second_sign_in_while_one_runs_is_refused_not_queued(fake, monkeypatch):
    """Security review: the page gave up on a slow Garmin, the athlete pressed
    Sign in again, and the second request waited on the lock and then sent a
    second round of logins - and a second code - to the same account."""
    import threading

    entered, release = threading.Event(), threading.Event()
    slow = FakeSignIn.login

    def held(self, tokenstore=None):
        entered.set()
        release.wait(5)
        return slow(self, tokenstore)

    monkeypatch.setattr(FakeSignIn, "login", held)
    login = auth.WebLogin()
    first = threading.Thread(target=login.start, args=("a@example.org", PASSWORD))
    first.start()
    assert entered.wait(5)
    second = login.start("a@example.org", PASSWORD)
    assert "already under way" in second["error"] and len(fake.made) == 1
    # ...and the page that stopped waiting can see it run, to follow it.
    assert second["busy"] is True and login.busy()
    assert "already under way" in login.finish("123456")["error"]
    release.set()
    first.join(5)
    assert login.waiting_for_code() and not login.busy()


def test_cancel_drops_the_pending_sign_in(fake):
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    login.cancel()
    assert not login.waiting_for_code() and login.finish("123456")["restart"] is True


def test_a_new_start_replaces_a_pending_one(fake):
    login = auth.WebLogin()
    login.start("a@example.org", PASSWORD)
    fake.mfa = False
    assert login.start("b@example.org", PASSWORD) == {"state": "signed_in"}
    assert not login.waiting_for_code()


@pytest.mark.parametrize("exc,says", [
    (GarminConnectAuthenticationError(f"401 for {PASSWORD}"), "did not accept that e-mail and password"),
    (GarminConnectTooManyRequestsError(f"429 {PASSWORD}"), "too many attempts"),
    (RuntimeError(f"strategy failed: {PASSWORD}"), "(RuntimeError)"),
])
def test_a_refusal_is_a_sentence_and_never_the_librarys_text(fake, exc, says):
    """The class decides the sentence. The library's message is for developers
    and could carry anything it was handed - it never reaches the page."""
    fake.fail_with = exc
    out = auth.WebLogin().start("a@example.org", PASSWORD)
    assert says in out["error"] and PASSWORD not in out["error"]
    assert not paths.garmin_session_present()


def test_the_terminal_and_the_page_show_the_same_notice(tmp_path):
    shown = auth.notice_points("~/.runspector/garmin")
    printed = " ".join(auth.LOGIN_NOTICE.format(store="~/.runspector/garmin").split())
    assert len(shown) == 4 and all(" ".join(p.split()) in printed for p in shown)


# ── POST /api/garmin/login ───────────────────────────────────────────────────

@pytest.fixture()
def live(tmp_path, fake, monkeypatch):
    """A real (not demo) app on an ephemeral port, with the sync stubbed: a
    sign-in starts one, and it must not reach out."""
    app = server.App.__new__(server.App)
    app.demo, app.token = False, None
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.reset_runtime_state()
    syncs = []
    monkeypatch.setattr(app, "refresh_start",
                        lambda after_sign_in=False: syncs.append(after_sign_in) or {"state": "started"})
    httpd = _serve(app)
    httpd.syncs = syncs
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_the_page_signs_in_in_two_steps_and_the_sync_follows(live):
    state = call(live, "GET", "/api/state")[1]
    assert state["garmin_session"] is False and state["garmin_login"]["busy"] is False
    assert any("never stores them" in p for p in state["garmin_login"]["notice"])

    status, data, _ = call(live, "POST", "/api/garmin/login",
                           {"email": " a@example.org ", "password": PASSWORD})
    assert status == 200 and data == {"ok": True, "state": "code"}
    assert call(live, "GET", "/api/state")[1]["garmin_login"]["waiting_for_code"] is True
    assert live.syncs == []

    status, data, _ = call(live, "POST", "/api/garmin/login", {"code": "123 456"})
    assert status == 200 and data["state"] == "signed_in" and data["sync"] == {"state": "started"}
    assert live.syncs == [True]                          # past the cooldown of a failed sync
    state = call(live, "GET", "/api/state")[1]
    assert state["garmin_session"] is True and state["garmin_login"]["waiting_for_code"] is False
    assert state["garmin_login"]["signins"] == 1
    assert PASSWORD not in json.dumps(state) and "secret-session" not in json.dumps(state)


def test_a_wrong_code_is_a_400_with_its_sentence_and_the_sign_in_stays_open(live):
    call(live, "POST", "/api/garmin/login", {"email": "a@example.org", "password": PASSWORD})
    status, data, _ = call(live, "POST", "/api/garmin/login", {"code": "999999"})
    assert status == 400 and "did not accept that code" in data["error"]
    assert call(live, "GET", "/api/state")[1]["garmin_login"]["waiting_for_code"] is True
    assert call(live, "POST", "/api/garmin/login", {"cancel": True})[0] == 200
    assert call(live, "GET", "/api/state")[1]["garmin_login"]["waiting_for_code"] is False


@pytest.mark.parametrize("body", [
    {}, {"email": "no-at-sign", "password": PASSWORD}, {"email": "a@example.org", "password": ""},
    {"email": "a@example.org", "password": 123}, {"email": "a@example.org", "password": "x" * 257},
    {"code": "abc123"}, {"code": 123456}, {"code": ""},
])
def test_a_malformed_sign_in_never_reaches_garmin(live, fake, body):
    status, data, _ = call(live, "POST", "/api/garmin/login", body)
    assert status == 400 and data["error"]
    assert fake.made == [] and live.syncs == []


def test_the_sign_in_refuses_a_cross_origin_request(live, fake):
    status, _, _ = call(live, "POST", "/api/garmin/login",
                        {"email": "a@example.org", "password": PASSWORD},
                        headers={"Origin": "https://evil.example"})
    assert status == 403 and fake.made == []


def test_a_password_is_taken_only_from_this_computer(fake):
    """With `--host` and a token the page is reachable from the phone - over
    plain HTTP. The connection's address decides, not a header."""
    app = server.App.__new__(server.App)
    app.demo = False
    app.reset_runtime_state()
    # 400, not 403: the page reads every 403 as a wrong access token and
    # drops it - the athlete would be signed out of the page for asking.
    out, status = app.garmin_sign_in({"email": "a@example.org", "password": PASSWORD}, local=False)
    assert status == 400 and "unencrypted" in out["error"] and fake.made == []

    peer = server.Handler._peer_is_local
    for addr, local in [("127.0.0.1", True), ("::1", True), ("::ffff:127.0.0.1", True),
                        ("192.168.1.20", False), ("10.0.0.5", False), ("::ffff:192.168.1.20", False),
                        ("fe80::1", False), ("not-an-address", False)]:
        assert peer(SimpleNamespace(client_address=(addr, 50000))) is local, addr


def test_the_demo_has_nothing_to_sign_in_to(fake):
    app = server.App.__new__(server.App)
    app.demo = True
    app.reset_runtime_state()
    out, status = app.garmin_sign_in({"email": "a@example.org", "password": PASSWORD}, local=True)
    assert status == 400 and fake.made == []


def test_a_sync_that_failed_for_want_of_a_session_offers_the_sign_in(tmp_path, monkeypatch):
    from runspector import garmin

    app = server.App.__new__(server.App)
    app.demo = False
    app.store = Store(str(tmp_path / "t.db"))
    app.reset_runtime_state()

    def expired(tokenstore=None):
        raise GarminConnectAuthenticationError("Username and password are required")

    monkeypatch.setattr(garmin, "login", expired)
    app._sync(app._sync_generation)
    assert app.last_sync["ok"] is False and app.last_sync["needs_sign_in"] is True
    # Fresh reader: the library raises its AuthenticationError for an offline
    # profile fetch too, with a valid token on disk. "Sign in again" alone sent
    # the athlete into a password-and-code round for a dead Wi-Fi; ↻ comes
    # first, the sign-in is the second step.
    assert "↻" in app.last_sync["reason"]
    assert app.last_sync["reason"].index("↻") < app.last_sync["reason"].index("sign in again")

    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: (_ for _ in ()).throw(OSError("disk")))
    paths.garmin_dir().mkdir(parents=True)
    (paths.garmin_dir() / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    app._sync(app._sync_generation)
    assert app.last_sync["needs_sign_in"] is False                  # ↻ is the answer there
