# Gmail OAuth setup

mail-agent reads Gmail messages using OAuth 2.0 with a single scope:

```
https://www.googleapis.com/auth/gmail.readonly
```

This scope cannot send, delete, modify labels, or draft email. No other
Gmail scope is ever requested.

## 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/ and create (or select) a project.
2. Enable the **Gmail API** for the project (APIs & Services → Library).

## 2. Configure the OAuth consent screen

1. APIs & Services → OAuth consent screen.
2. User type: **External** (or **Internal** if using a Google Workspace org).
3. Add your own Google account as a test user if the app stays in "Testing"
   publishing status (fine for personal/local use — avoids Google's app
   review, since only you will authorize it).
4. Do **not** add any scope other than `gmail.readonly` on the consent screen.

## 3. Create an OAuth client ID

1. APIs & Services → Credentials → Create Credentials → OAuth client ID.
2. Application type: **Desktop app**.
3. Download the resulting JSON — this is your `credentials.json`.

## 4. Place the credentials file

Put the downloaded file where `GMAIL_CREDENTIALS_FILE` points (default:
`credentials.json` in the working directory). This file identifies the OAuth
*client*, not a user — it is not a secret token, but keep it out of version
control regardless (already covered by `.gitignore`).

## 5. First run — interactive authorization

On first use, the app opens a browser window (via
`InstalledAppFlow.run_local_server`) asking you to sign in and approve
**read-only Gmail access**. After approval, the resulting token is written
to the path configured by `GMAIL_TOKEN_FILE`.

By default this token is stored **outside the repository**, under
`~/.config/mail-agent/token.json`, with file permissions restricted to the
owner (`chmod 600`) on POSIX systems. Set `GMAIL_TOKEN_FILE` to an absolute
path to control this explicitly.

Subsequent runs reuse the stored token (refreshing it silently) and do not
require re-authorization unless the token is revoked or deleted.

## 6. Verifying scopes

At startup, if a token file already exists, the app inspects its granted
scopes and refuses to start if anything other than `gmail.readonly` is
present. If you ever see a scope-related startup error, delete the token
file and re-run the authorization flow — do not edit the token file by hand.

## Revoking access

You can revoke mail-agent's access at any time from
https://myaccount.google.com/permissions. Delete the local token file
afterward so the app doesn't attempt to use a revoked token.
