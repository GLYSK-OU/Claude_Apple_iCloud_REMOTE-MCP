"""Shared HTML chrome for the pages a human actually sees.

Three flows render pages — the OAuth consent screen, the hosted admin sign-in,
and the local sign-in that Claude Desktop uses — and they should look like one
product. The markup is deliberately plain: no scripts and no external
resources, which is what lets the Content-Security-Policy be as strict as it
is.
"""

from __future__ import annotations

import html

from starlette.responses import HTMLResponse

from .security import SECURITY_HEADERS

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f5f5f7; color: #1d1d1f; padding: 24px; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16161a; color: #f2f2f4; }} }}
  .card {{ background: #fff; border-radius: 14px; padding: 32px; max-width: 460px; width: 100%;
           box-shadow: 0 1px 3px rgba(0,0,0,.12), 0 8px 28px rgba(0,0,0,.08); }}
  @media (prefers-color-scheme: dark) {{ .card {{ background: #232329; }} }}
  h1 {{ font-size: 21px; margin: 0 0 6px; }}
  p {{ margin: 0 0 18px; color: #56565c; font-size: 14px; }}
  @media (prefers-color-scheme: dark) {{ p {{ color: #a1a1a8; }} }}
  label {{ display: block; font-size: 13px; font-weight: 600; margin: 14px 0 6px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 11px 12px; font-size: 16px;
           border: 1px solid #d2d2d7; border-radius: 9px; background: transparent; color: inherit; }}
  button {{ margin-top: 20px; width: 100%; padding: 12px; font-size: 16px; font-weight: 600;
            border: 0; border-radius: 9px; background: #0071e3; color: #fff; cursor: pointer; }}
  button.secondary {{ background: transparent; color: #56565c; font-weight: 500; margin-top: 8px; }}
  .error {{ background: #fff1f0; border: 1px solid #ffccc7; color: #a8071a; padding: 11px 13px;
            border-radius: 9px; font-size: 14px; margin-bottom: 16px; }}
  .ok {{ background: #f0fff4; border: 1px solid #b7ebc6; color: #135200; padding: 11px 13px;
         border-radius: 9px; font-size: 14px; margin-bottom: 16px; }}
  .note {{ font-size: 12.5px; color: #86868b; margin-top: 20px; }}
  code {{ font-size: 12.5px; background: rgba(127,127,127,.14); padding: 1px 5px; border-radius: 4px; }}
</style></head><body><div class="card">{body}</div></body></html>
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=body),
        status_code=status,
        headers=dict(SECURITY_HEADERS),
    )


def alert(message: str, kind: str = "error") -> str:
    return f'<div class="{kind}">{html.escape(message)}</div>'
