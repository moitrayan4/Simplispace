# Simplispace AI server

A Cloudflare Worker between the app and Groq. It holds the Groq key, checks that the caller is
signed in to Simplispace with Google, and applies limits. It accepts only a sender's name, domain and
counts, and it logs and stores none of them. For second opinions it looks the company up on its own
website and Wikipedia first (4 seconds at most).

## Deploy

Needs a free Cloudflare account, a project-owned Groq key, and Node.js 22+. Run in `server/`:

1. `npm install`
2. In `wrangler.jsonc`, set `GOOGLE_CLIENT_ID` to the app's **Desktop** client ID (not the secret).
3. Test: `npm test`
4. `npx wrangler login`
5. `npx wrangler deploy`, then note the `https://simplispace-ai.<you>.workers.dev` address.
   AI requests are refused until the secrets below exist.
6. `npx wrangler secret put GROQ_API_KEY`, and paste the key when asked.
7. `npx wrangler secret put QUOTA_SECRET`, and paste a long random value, for example from
   `node -p "crypto.randomBytes(32).toString('hex')"`. Keep it the same afterwards.
8. Open `https://.../health`. It should show `{"ok":true}`.

Never put either secret in a file in this repo.

In Google Cloud Console, add the `openid` scope under *Google Auth Platform*, *Data Access*. In the
Groq console, turn on Zero Data Retention under Data Controls.

Then set `AI_SERVER_URL=https://simplispace-ai.<you>.workers.dev` in `backend/.env` and rebuild the
exe. Users who were already signed in must log out and sign in once.

## Operate

- **Limits:** `USER_DAILY`, `USER_MINUTE`, `GLOBAL_DAILY` and `GLOBAL_MINUTE` in `wrangler.jsonc`.
  Keep the global ones under your Groq plan's limits, and set a spending limit in Groq too.
- **Turn it off:** set `AI_ENABLED` to `"false"` and deploy. To cut it off at once, revoke the Groq key.
- **Replace a leaked key:** revoke it in Groq, then run `npx wrangler secret put GROQ_API_KEY` again.
