"""Shared HTML chrome for the pages a human actually sees.

Three flows render pages — the OAuth consent screen, the hosted admin sign-in,
and the loopback sign-in Claude Desktop uses — and they should look like one
product rather than three utilities.

The markup is deliberately plain: no external resources of any kind, which is
what lets the Content-Security-Policy stay as tight as it is. Everything
below is inlined.
"""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse

from .security import LOCAL_PAGE_HEADERS, SECURITY_HEADERS

BRAND = "GLYSK"
PRODUCT = "Claude \u21c4 Apple iCloud"

# The product mark, drawn rather than fetched: a document inside a cloud.
LOGO = """
<svg class="mark" viewBox="0 0 48 48" role="img" aria-label="GLYSK">
  <rect width="48" height="48" rx="11" fill="var(--brand)"/>
  <ellipse cx="18" cy="25" rx="8.5" ry="8.5" fill="#fff"/>
  <ellipse cx="25" cy="21" rx="10" ry="10" fill="#fff"/>
  <ellipse cx="32" cy="25" rx="7" ry="7" fill="#fff"/>
  <rect x="10" y="24" width="28" height="8" rx="4" fill="#fff"/>
  <rect x="19.5" y="19" width="11" height="15" rx="2" fill="var(--brand)"/>
  <rect x="19.5" y="19" width="11" height="15" rx="2" fill="none" stroke="#fff" stroke-width="1.6"/>
  <rect x="22" y="23" width="6" height="1.4" rx="0.7" fill="#fff"/>
  <rect x="22" y="26" width="6" height="1.4" rx="0.7" fill="#fff"/>
  <rect x="22" y="29" width="6" height="1.4" rx="0.7" fill="#fff"/>
</svg>
"""

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title} · {brand}</title>
<style>
  :root {{
    --brand: #2f5d8a;
    --brand-ink: #1d3d5c;
    --paper: #eef1f5;
    --card: #ffffff;
    --ink: #141c26;
    --muted: #5d6875;
    --hair: #dbe1e8;
    --ok: #1f6b52;
    --ok-bg: #e6f4ee;
    --warn: #8a5a12;
    --warn-bg: #fbf1de;
    --bad: #9d2f2a;
    --bad-bg: #fbeceb;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --brand: #6fa3d8; --brand-ink: #a8c9ea;
      --paper: #0f141a; --card: #19212b; --ink: #e8edf3; --muted: #97a4b3; --hair: #2b3641;
      --ok: #6fc7a5; --ok-bg: #17302a; --warn: #d9a95c; --warn-bg: #31281a; --bad: #e08e88; --bad-bg: #331f1e;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; padding: 32px 20px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: var(--paper); color: var(--ink);
    font-family: var(--font); font-size: 15px; line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}
  .card {{
    background: var(--card); width: 100%; max-width: 460px;
    border: 1px solid var(--hair); border-radius: 16px; padding: 30px 32px 28px;
    box-shadow: 0 1px 2px rgba(20,28,38,.05), 0 18px 44px rgba(20,28,38,.09);
  }}
  .brandbar {{ display: flex; align-items: center; gap: 11px; margin-bottom: 24px; }}
  .mark {{ width: 34px; height: 34px; flex: none; display: block; }}
  .brandname {{ font-weight: 700; font-size: 15px; letter-spacing: .01em; }}
  .product {{ font-size: 12px; color: var(--muted); }}
  h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -.01em; margin: 0 0 8px; line-height: 1.25; }}
  p {{ margin: 0 0 16px; color: var(--muted); font-size: 14px; }}
  p.lead {{ color: var(--ink); }}
  label {{ display: block; font-size: 12.5px; font-weight: 600; margin: 16px 0 6px; }}


  /* ---- scope notice + disclosures ------------------------------------ */
  .notice {{ display:flex; gap:10px; align-items:flex-start; margin:16px 0 4px;
             padding:12px 14px; border-radius:10px; font-size:13px; line-height:1.5;
             background:var(--warn-bg); color:var(--warn);
             border:1px solid color-mix(in srgb, var(--warn) 25%, transparent); }}
  .notice .ico {{ flex:0 0 auto; font-size:15px; line-height:1.35; }}
  details.more {{ margin-top:18px; border-top:1px solid var(--hair); padding-top:12px; }}
  details.more > summary {{ cursor:pointer; font-size:13px; font-weight:600;
                            color:var(--muted); list-style:none; padding:4px 0; }}
  details.more > summary::-webkit-details-marker {{ display:none; }}
  details.more > summary::before {{ content:"\25B8 "; display:inline-block;
                                    transition:transform .15s ease; }}
  details.more[open] > summary::before {{ transform:rotate(90deg); }}
  details.more[open] > summary {{ color:var(--ink); }}
  /* ---- service picker ---------------------------------------------- */
  .svc {{ list-style:none; padding:0; margin:14px 0 0; }}
  .svc li {{ display:flex; align-items:flex-start; gap:12px; padding:11px 2px;
             border-bottom:1px solid var(--hair); }}
  .svc li:last-child {{ border-bottom:0; }}
  .svc .ico {{ flex:0 0 30px; width:30px; height:30px; border-radius:7px;
               display:grid; place-items:center; font-size:16px; line-height:1;
               background:var(--paper); }}
  .svc .txt {{ flex:1 1 auto; min-width:0; }}
  .svc .nm {{ font-weight:600; font-size:14px; }}
  .svc .ds {{ font-size:12.5px; color:var(--muted); margin-top:2px; }}
  .svc .off .nm, .svc .off .ds {{ color:var(--muted); }}
  .svc .off .ico {{ opacity:.4; }}
  /* An iOS switch, built from a checkbox so it works with no JavaScript. */
  .sw {{ flex:0 0 auto; position:relative; width:46px; height:28px; margin-top:2px; }}
  .sw input {{ position:absolute; inset:0; opacity:0; margin:0; cursor:pointer; }}
  .sw span {{ position:absolute; inset:0; border-radius:999px; background:#d6d6da;
              transition:background .18s ease; pointer-events:none; }}
  .sw span::after {{ content:""; position:absolute; top:2px; left:2px; width:24px; height:24px;
                     border-radius:50%; background:#fff; transition:transform .18s ease;
                     box-shadow:0 1px 3px rgba(0,0,0,.28); }}
  .sw input:checked + span {{ background:#34c759; }}
  .sw input:checked + span::after {{ transform:translateX(18px); }}
  .sw input:focus-visible + span {{ outline:2px solid var(--brand); outline-offset:2px; }}
  .sw input:disabled {{ cursor:not-allowed; }}
  .sw input:disabled + span {{ background:#e6e6ea; }}
  .sw input:disabled + span::after {{ box-shadow:none; opacity:.6; }}
  .locked {{ font-size:11px; color:var(--muted); border:1px solid var(--hair);
             border-radius:999px; padding:3px 9px; white-space:nowrap; margin-top:5px; }}
  .allbar {{ display:flex; align-items:center; justify-content:space-between; gap:12px;
             padding:13px 14px; border:1px solid var(--hair); border-radius:12px;
             background:var(--paper); margin-top:16px; }}
  .allbar .nm {{ font-weight:650; font-size:14px; }}
  .allbar .ds {{ font-size:12.5px; color:var(--muted); margin-top:2px; }}
  @media (prefers-color-scheme: dark) {{
    .sw span {{ background:#3a3a3c; }}
    .sw input:disabled + span {{ background:#2c2c2e; }}
  }}

  input[type=email], input[type=password], input[type=text] {{
    width: 100%; padding: 11px 13px; font-size: 16px; font-family: inherit;
    border: 1px solid var(--hair); border-radius: 10px;
    background: var(--card); color: var(--ink);
  }}
  input:focus-visible, button:focus-visible {{ outline: 2px solid var(--brand); outline-offset: 2px; }}
  button {{
    margin-top: 22px; width: 100%; padding: 12px; font-size: 15px; font-weight: 600;
    font-family: inherit; border: 0; border-radius: 10px;
    background: var(--brand); color: #fff; cursor: pointer;
  }}
  button.secondary {{ background: transparent; color: var(--muted); font-weight: 500; margin-top: 6px; }}
  .alert {{ padding: 11px 14px; border-radius: 10px; font-size: 13.5px; margin-bottom: 16px; }}
  .alert.error {{ background: var(--bad-bg); color: var(--bad); }}
  .alert.ok {{ background: var(--ok-bg); color: var(--ok); }}
  .alert.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .note {{ font-size: 12.5px; color: var(--muted); margin-top: 18px; }}
  code {{ font-family: var(--mono); font-size: .9em; background: rgba(127,127,127,.14);
          padding: 1px 5px; border-radius: 4px; }}
  .panel {{ border: 1px solid var(--hair); border-radius: 12px; padding: 2px 16px; margin: 4px 0 18px; }}
  .panel h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
               color: var(--muted); margin: 14px 0 10px; }}
  ul.perms {{ list-style: none; margin: 0 0 14px; padding: 0; }}
  ul.perms li {{ display: flex; gap: 10px; font-size: 13.5px; padding: 5px 0; align-items: flex-start; }}
  ul.perms li .g {{ flex: none; width: 16px; font-weight: 700; line-height: 1.5; }}
  li.yes .g {{ color: var(--brand); }}
  li.no .g {{ color: var(--ok); }}
  li.warn .g {{ color: var(--warn); }}
  li.warn span:last-child {{ color: var(--ink); }}
  .footer {{ margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--hair);
             font-size: 11.5px; color: var(--muted); text-align: center; }}
  .codes {{ display: flex; gap: 8px; justify-content: space-between; margin-top: 8px; }}
  .codes input {{
    width: 100%; aspect-ratio: 3 / 4; text-align: center; padding: 0;
    font-size: 26px; font-weight: 600; font-family: var(--mono);
    border: 1px solid var(--hair); border-radius: 11px;
    background: var(--card); color: var(--ink);
  }}
  .codes input:focus {{ border-color: var(--brand); outline: none;
                        box-shadow: 0 0 0 3px rgba(47,93,138,.16); }}
  .host {{ display: inline-flex; align-items: center; gap: 6px; font-family: var(--mono);
           font-size: 12px; color: var(--ok); background: var(--ok-bg);
           padding: 4px 9px; border-radius: 20px; margin-bottom: 16px; }}
</style></head><body>
<div class="card">
  <div class="brandbar">{logo}<div><div class="brandname">{brand}</div>
  <div class="product">{product}</div></div></div>
  {body}
  <div class="footer">{brand} · open source · your credentials stay on your machine</div>
</div>
{script}</body></html>
"""


def page(
    title: str,
    body: str,
    status: int = 200,
    script: str = "",
    local: bool = False,
    form_action: str = "",
) -> HTMLResponse:
    """Render a branded page. `local` relaxes the CSP just enough for the code boxes.

    `form_action` names one extra origin a form on this page may end up at.
    `form-action` governs the whole navigation a submit starts, redirects
    included, so a page whose POST answers with a 302 elsewhere must name that
    destination or the browser cancels the navigation — with no error the user
    can see. The consent page is exactly that: it redirects to the OAuth
    client's callback.
    """
    # `script` is interpolated into the body verbatim, so a caller that omits
    # the tags gets its JavaScript rendered as visible text — the page looks
    # broken and nothing runs. That shipped once. Refuse it rather than repeat
    # it: this is a programming error, not a runtime condition.
    if script and not script.lstrip().startswith("<script"):
        raise ValueError(
            "page(script=...) must be a complete <script>…</script> block; "
            "bare JavaScript is interpolated as text and never executes."
        )

    headers = dict(LOCAL_PAGE_HEADERS if local else SECURITY_HEADERS)
    if form_action:
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace(
            "form-action 'self'", f"form-action 'self' {form_action}"
        )
    return HTMLResponse(
        _PAGE.format(
            title=html.escape(title),
            brand=BRAND,
            product=PRODUCT,
            logo=LOGO,
            body=body,
            script=script,
        ),
        status_code=status,
        headers=headers,
    )


def alert(message: str, kind: str = "error") -> str:
    return f'<div class="alert {kind}">{html.escape(message)}</div>'


def scope_notice() -> str:
    """The one sentence that must never be collapsed.

    Everything else on this page can fold away. This cannot: it is the fact
    that makes the switch list below a real choice rather than decoration.
    """
    return """
    <div class="notice">
      <span class="ico" aria-hidden="true">&#9888;</span>
      <span><strong>Apple has no Drive-only login.</strong> The session created here can reach
      Photos, Contacts, Calendar, Reminders, Notes and Find My as well. This software refuses
      every service you leave switched off below &mdash; in code, not as a policy &mdash; but the
      session itself stays un-restricted. If that matters, use a separate Apple ID.</span>
    </div>
    """


def permissions_panel() -> str:
    """Spell out what signing in actually grants.

    An earlier version of this claimed the session could not reach Photos,
    Contacts or Find My. That was wrong: Apple issues one un-scoped session for
    iCloud web, and `pyicloud` exposes photos, contacts, calendar, reminders,
    notes, devices and hidemyemail from the very same object this uses for
    Drive. Saying otherwise on a consent screen is worse than saying nothing,
    so the panel now separates what this software does from what the session
    permits.
    """
    return """
    <div class="panel">
      <h2>What this software does</h2>
      <ul class="perms">
        <li class="yes"><span class="g">+</span><span><strong>iCloud Drive only.</strong> It can
          read, create, change, move and delete files in the Drive folder you allow, and
          nothing else. Deletions go to Recently Deleted for 30 days.</span></li>
      </ul>
      <h2>What signing in actually grants</h2>
      <ul class="perms">
        <li class="warn"><span class="g">!</span><span>Apple does not offer a Drive-only login.
          The session created here is a <strong>general iCloud session</strong>: it could also
          reach Photos, Contacts, Calendar, Reminders, Notes and Find My. This software refuses
          every one of them &mdash; not as a policy but in code, so a mistake or a later change
          cannot quietly widen it. The <em>session</em> is still un-restricted, though, so
          anyone who took control of this computer could use it directly.</span></li>
        <li class="warn"><span class="g">!</span><span>If that matters to you, sign in with a
          <strong>separate Apple ID</strong> that only has the folder you want to share.</span></li>
      </ul>
      <h2>What stays out of reach</h2>
      <ul class="perms">
        <li class="no"><span class="g">&minus;</span><span>Passwords in iCloud Keychain, Apple Pay
          and payment methods, and iMessage content. Apple end-to-end encrypts these and does not
          expose them to the web service this uses.</span></li>
        <li class="no"><span class="g">&minus;</span><span>Your Apple password is never written to
          disk and never sent to Claude or to GLYSK. It goes to Apple to create the session, and
          is discarded.</span></li>
      </ul>
    </div>
    """


_CODE_SCRIPT = """<script>
(function () {
  var form = document.getElementById('f');
  if (!form) return;
  var boxes = Array.prototype.slice.call(form.querySelectorAll('.codes input'));
  var last = boxes.length - 1;

  // Spread a multi-character value across the boxes. iOS offers the SMS code
  // above the keyboard and autofills the whole thing into one field, which
  // maxlength would otherwise reduce to a single digit.
  function spread(digits, from) {
    for (var j = 0; j < boxes.length; j++) {
      if (j >= from) boxes[j].value = digits.charAt(j - from) || '';
    }
    boxes[Math.min(from + digits.length, last)].focus();
  }

  boxes.forEach(function (box, i) {
    box.addEventListener('input', function () {
      var digits = box.value.replace(/[^0-9]/g, '');
      if (digits.length > 1) { spread(digits, i); return; }
      box.value = digits;
      if (digits && i < last) boxes[i + 1].focus();
    });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Backspace' && !box.value && i > 0) boxes[i - 1].focus();
    });
    box.addEventListener('focus', function () { box.select(); });
    box.addEventListener('paste', function (e) {
      var text = (e.clipboardData || window.clipboardData).getData('text') || '';
      var digits = text.replace(/[^0-9]/g, '').slice(0, boxes.length);
      if (!digits) return;
      e.preventDefault();
      spread(digits, i);
    });
  });

  // The autofocus attribute can land after the first keystroke, which silently
  // drops the leading digit. Take focus explicitly once the page is ready.
  if (boxes[0]) { try { boxes[0].focus(); } catch (err) {} }

  form.addEventListener('submit', function () {
    var joined = document.createElement('input');
    joined.type = 'hidden';
    joined.name = 'code';
    joined.value = boxes.map(function (b) { return b.value; }).join('');
    form.appendChild(joined);
  });
})();
</script>"""


# Apple's own glyphs are trademarks and are not ours to ship, so each service
# gets a plain emoji that reads at a glance. Recognisable, unambiguous, and
# nobody's intellectual property to borrow.
SERVICE_ICONS: dict[str, str] = {
    "drive": "\u2601\ufe0f",
    "photos": "\U0001f33a",
    "calendar": "\U0001f4c5",
    "reminders": "\u2611\ufe0f",
    "contacts": "\U0001f465",
    "notes": "\U0001f4dd",
    "devices": "\U0001f4cd",
    "hidemyemail": "\U0001f3ad",
    "account": "\u2699\ufe0f",
    "files": "\U0001f4c4",
    "invites": "\u2709\ufe0f",
    "mail": "\u2709\ufe0f",
    "messages": "\U0001f4ac",
    "keychain": "\U0001f511",
    "wallet": "\U0001f4b3",
    "health": "\u2764\ufe0f",
    "music": "\U0001f3b5",
    "tv": "\U0001f4fa",
    "news": "\U0001f4f0",
    "arcade": "\U0001f579\ufe0f",
}


def service_picker(granted: frozenset[str] | None = None) -> str:
    """The Apple-style switch list for choosing what Claude may reach.

    Signing in to Apple hands over one un-scoped session. This is where that
    becomes a decision instead of a side effect: everything is off but Drive
    until someone turns it on, and the switches are the grant, not a
    preference the code may later ignore.

    Services this build cannot reach are shown anyway, switched off and
    disabled with the reason beside them. Hiding them would leave a reader to
    assume Wallet and Messages were quietly included.
    """
    from .services import AVAILABLE, DRIVE, UNAVAILABLE

    granted = granted if granted is not None else frozenset({DRIVE})
    rows: list[str] = []

    for service in AVAILABLE:
        icon = SERVICE_ICONS.get(service.key, "\u2022")
        checked = " checked" if service.key in granted else ""
        # Drive is why the connector exists; turning it off leaves nothing.
        fixed = service.key == DRIVE
        control = (
            f'<input type="checkbox" name="services" value="{html.escape(service.key)}" '
            f'aria-label="{html.escape(service.name)}"{checked}'
            f"{' disabled' if fixed else ''}>"
        )
        hidden = f'<input type="hidden" name="services" value="{html.escape(DRIVE)}">' if fixed else ""
        rows.append(
            f'<li><span class="ico" aria-hidden="true">{icon}</span>'
            f'<span class="txt"><span class="nm">{html.escape(service.name)}</span>'
            f'<span class="ds">{html.escape(service.summary)}</span></span>'
            f'<label class="sw">{control}<span></span></label>{hidden}</li>'
        )

    unreachable: list[str] = []
    for service in UNAVAILABLE:
        icon = SERVICE_ICONS.get(service.key, "\u2022")
        unreachable.append(
            f'<li class="off"><span class="ico" aria-hidden="true">{icon}</span>'
            f'<span class="txt"><span class="nm">{html.escape(service.name)}</span>'
            f'<span class="ds">{html.escape(service.unavailable_because)}</span></span>'
            f'<span class="locked">Not available</span></li>'
        )

    return f"""
      <div class="allbar">
        <span><span class="nm">Everything above</span>
        <span class="ds">Turns on every service this connector can reach.</span></span>
        <label class="sw"><input type="checkbox" id="all" aria-label="Select every service">
        <span></span></label>
      </div>
      <ul class="svc">{"".join(rows)}</ul>
      <details class="more">
        <summary>{len(unreachable)} more Apple services, and why they are out of reach</summary>
        <ul class="svc">{"".join(unreachable)}</ul>
      </details>
    """


_PICKER_SCRIPT = """<script>
(function () {
  var all = document.getElementById('all');
  if (!all) return;
  var boxes = Array.prototype.slice.call(
    document.querySelectorAll('.svc input[type=checkbox]:not([disabled])')
  );
  function sync() {
    all.checked = boxes.length > 0 && boxes.every(function (b) { return b.checked; });
  }
  all.addEventListener('change', function () {
    boxes.forEach(function (b) { b.checked = all.checked; });
  });
  boxes.forEach(function (b) { b.addEventListener('change', sync); });
  sync();
})();
</script>
"""


def signin_password_page(
    apple_id: str,
    action: str,
    message: str = "",
    local: bool = False,
    granted: frozenset[str] | None = None,
) -> HTMLResponse:
    """The first sign-in screen, shared by the local and hosted flows.

    One page rather than two: this is where someone decides to hand over an
    Apple password, and it should say the same things wherever it is served
    from.

    It is also where the grant is chosen. Apple hands over one un-scoped
    session, so the only honest place to ask which services Claude may reach is
    the moment that session is created — not a settings page discovered later.
    """
    where = (
        "localhost &mdash; this page is running on your own computer"
        if local
        else "your own server &mdash; this page is served by the connector you deployed"
    )
    return page(
        "Connect Apple iCloud",
        f"""
        <div class="host">&#x1F512; {where}</div>
        <h1>Connect Claude to your Apple iCloud</h1>
        <p class="lead">Sign in with Apple, then choose what Claude may reach. Your password
           goes straight to Apple from here — it is never stored, never sent to Claude, and
           never appears in your conversation.</p>
        {alert(message) if message else ""}
        {scope_notice()}
        <form method="post" action="{action}">
          <input type="hidden" name="step" value="password">
          <label for="apple_id">Apple ID</label>
          <input id="apple_id" name="apple_id" type="email" value="{html.escape(apple_id)}"
                 autocomplete="username" required>
          <label for="password">Apple ID password</label>
          <input id="password" name="password" type="password"
                 autocomplete="current-password" required>
          <h2>What Claude may reach</h2>
          <p class="note" style="margin-top:0">Everything is off except iCloud Drive. Turn on
             only what you want, and change it any time by signing in again.</p>
          {service_picker(granted)}
          <button type="submit">Continue to Apple</button>
        </form>
        <p class="note"><strong>Use your account's real password.</strong> An app-specific
           password will not work: Apple accepts those only for Mail, Contacts, Calendar and
           Reminders, never for iCloud Drive. GLYSK never receives it.</p>
        <details class="more">
          <summary>Exactly what this can and cannot touch</summary>
          {permissions_panel()}
        </details>
        """,
        script=_PICKER_SCRIPT,
        local=True,
    )


def signin_code_page(action: str, message: str = "", delivery: str = "") -> HTMLResponse:
    """Apple-style six-box code entry, shared by both flows."""
    boxes = "".join(
        # No maxlength: it truncates an autofilled code to one character before
        # any script can see it, and that is exactly how iOS delivers the SMS
        # code. The script keeps one digit per box instead.
        f'<input name="d{i}" inputmode="numeric" pattern="[0-9]*" '
        f'autocomplete="{"one-time-code" if i == 0 else "off"}" '
        f'aria-label="Digit {i + 1} of 6"{" autofocus" if i == 0 else ""}>'
        for i in range(6)
    )
    kind = "ok" if "sent" in message.lower() else "error"
    # Apple pushes the code to trusted devices as part of the sign-in itself,
    # before anything here asks it to. Saying "we sent you a code" invites the
    # user to wait for a second one that is never coming, so point them at the
    # devices instead.
    sent_where = {
        "sms": "Apple has sent a six-digit code by SMS. Check your phone.",
    }.get(
        delivery,
        "Apple sends the code to your trusted Apple devices as you sign in. "
        "Check them now — it may already be waiting.",
    )
    route = (
        f'<p class="note">Apple is delivering this by <strong>{html.escape(delivery)}</strong>. '
        "Send a new code asks for another; Apple often declines that if one is already in "
        "flight, or if there have been several attempts recently.</p>"
        if delivery and delivery != "unknown"
        else ""
    )
    return page(
        "Verification code",
        f"""
        <h1>Enter the code Apple sent</h1>
        {alert(message, kind) if message else f'<p class="lead">{sent_where}</p>'}
        <form method="post" action="{action}" id="f">
          <input type="hidden" name="step" value="code">
          <div class="codes">{boxes}</div>
          <button type="submit">Verify and connect</button>
        </form>
        <form method="post" action="{action}">
          <input type="hidden" name="step" value="resend">
          <button type="submit" class="secondary">Send a new code</button>
        </form>
        {route}
        <p class="note">Apple sent this code, not GLYSK. If you did not just start this
           sign-in, close this page and change your Apple ID password.</p>
        """,
        script=_CODE_SCRIPT,
        local=True,
    )


def signin_done_page(message: str, extra: str = "") -> HTMLResponse:
    """The confirmation screen, shared by both flows."""
    return page(
        "Connected",
        f"""
        <h1>iCloud Drive is connected</h1>
        {alert(message, "ok")}
        <p class="lead">You can close this tab. Claude will confirm in your conversation.</p>
        {extra}
        <p class="note">Apple ends this session after about 30 days and offers no way to
           extend it without a new code — Apple's policy, not this software's. When it
           lapses, ask Claude to sign in to iCloud Drive again.</p>
        <p class="note">To revoke access sooner, remove this device under Apple ID &rsaquo;
           Devices, or delete the session directory on the server.</p>
        """,
        local=True,
    )


__all__ = [
    "page",
    "alert",
    "permissions_panel",
    "scope_notice",
    "service_picker",
    "signin_password_page",
    "signin_code_page",
    "signin_done_page",
    "BRAND",
    "PRODUCT",
    "LOGO",
]
