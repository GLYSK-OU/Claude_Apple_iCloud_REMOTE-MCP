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
PRODUCT = "iCloud Drive for Claude"

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


def page(title: str, body: str, status: int = 200, script: str = "", local: bool = False) -> HTMLResponse:
    """Render a branded page. `local` relaxes the CSP just enough for the code boxes."""
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
        headers=dict(LOCAL_PAGE_HEADERS if local else SECURITY_HEADERS),
    )


def alert(message: str, kind: str = "error") -> str:
    return f'<div class="alert {kind}">{html.escape(message)}</div>'


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
          reach Photos, Contacts, Calendar, Reminders, Notes and Find My. This software never
          calls them, but the session is not restricted to Drive, so anyone who took control of
          this computer could.</span></li>
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


__all__ = ["page", "alert", "permissions_panel", "BRAND", "PRODUCT", "LOGO"]
