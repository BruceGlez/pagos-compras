# Gmail OAuth Guardrails (send + inbox read)

Date: 2026-03-16
Owner: pagos-compras ops

## Required scopes
- https://www.googleapis.com/auth/gmail.send
- https://www.googleapis.com/auth/gmail.modify

## Canonical auth command
Always use:

```bash
python manage.py autorizar_gmail_oauth
```

This command must request BOTH scopes.

## Token location
- `/home/bruce/.openclaw/workspace/.secrets/gmail_oauth_token.json`

## Do not overwrite token with send-only scope
If any helper/script asks only for `gmail.send`, DO NOT run it in production.

## Verification checklist
After any reauth:
1. Open token json and verify both scopes are present.
2. Test inbox action in app: `Leer inbox ahora`.
3. If 403 insufficient scopes appears, re-run canonical auth command.

## Incident note (2026-03-16)
Issue: inbox read failed with 403 insufficient scopes.
Root cause: token was overwritten with send-only scope.
Resolution: reauthorized with both scopes and documented guardrails.
