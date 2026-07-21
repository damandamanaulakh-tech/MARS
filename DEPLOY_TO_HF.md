# Deploy to Hugging Face Spaces — iPad guide

Total time: ~10 minutes. Done once. After that, every push to `main`
auto-deploys.

## Step 1: Create your Hugging Face Space (3 min)

1. On iPad Safari, open **https://huggingface.co/new-space**
2. Fill in:
   - **Space name**: `sourceborn` (or anything you like — lowercase, no spaces)
   - **License**: MIT
   - **Select the Space SDK**: tap **Gradio**
   - **Space hardware**: leave as **CPU basic (Free)**
   - **Public or private**: tap **Private** (recommended — keeps your
     trading config from being indexed by Google)
3. Tap the blue **Create Space** button at the bottom
4. You'll land on the new (empty) Space page. **Copy the URL** from the
   address bar — it looks like:
   `https://huggingface.co/spaces/YOUR_USERNAME/sourceborn`
5. Note down `YOUR_USERNAME/sourceborn` — you need this in Step 3 as
   `HF_SPACE`

## Step 2: Get a write token from Hugging Face (2 min)

1. Tap your profile picture (top right) → **Settings**
2. In the left menu, tap **Access Tokens**
3. Tap **+ Create new token**
4. Fill in:
   - **Token name**: `github-sync`
   - **Token type**: tap **Write**
5. Tap **Create token**
6. **Copy the token** (starts with `hf_...`). You will not see it again.
   Paste it somewhere safe for the next step.

## Step 3: Add the token to GitHub (2 min)

1. On iPad Safari, open
   **https://github.com/damandamanaulakh-tech/sourceborn-engine/settings/secrets/actions**
   (you may need to sign in)
2. Tap the green **New repository secret** button
3. Fill in:
   - **Name**: `HF_TOKEN`
   - **Secret**: paste the token from Step 2
4. Tap **Add secret**
5. Tap **New repository secret** again
6. Fill in:
   - **Name**: `HF_SPACE`
   - **Secret**: paste `YOUR_USERNAME/sourceborn` from Step 1 (the
     short form, no `https://...`)
7. Tap **Add secret**

You should now see two secrets in the list: `HF_SPACE` and `HF_TOKEN`.

## Step 4: Trigger the first deploy (1 min)

The auto-sync workflow is already in `.github/workflows/sync-to-hf.yml`.
It runs every time you push to `main`. To trigger it the first time,
merge this branch:

1. On iPad Safari, open
   **https://github.com/damandamanaulakh-tech/sourceborn-engine/pulls**
2. If you don't see a PR for `claude/live-swing-engine`, tap
   **New pull request**, pick the branch, **Create pull request**
3. Tap **Merge pull request** → **Confirm merge**
4. As soon as the merge happens, the GitHub Action runs (you can watch
   it under the **Actions** tab — should finish in ~30 seconds)

## Step 5: Wait for the Space to build (3 min)

1. Open your Space URL from Step 1
   (`https://huggingface.co/spaces/YOUR_USERNAME/sourceborn`)
2. You'll see **Building** at the top — it's installing `gradio`,
   `yfinance`, etc. Takes ~3 minutes on the free tier.
3. When it says **Running**, the app is live.

## Step 6: Use it

1. The chat appears at the top (your existing Sourceborn chat)
2. Scroll down — you'll see tabs starting with **Trading: Summary**
3. Tap **Trading: Summary**
4. Tap **Run pre-market scan now**
5. Wait ~30 seconds. The picks will fill in.

That's it. From now on, every commit to `main` automatically syncs to
your HF Space. No manual upload needed, ever.

---

## Troubleshooting

**"GitHub Action failed"**
- Open the **Actions** tab on your GitHub repo
- Tap the failed run → look for red text
- Most common cause: `HF_TOKEN` or `HF_SPACE` is misspelled. Re-check
  Step 3.

**"Space says Build failed"**
- On the Space page, tap **Logs** (top right)
- If a dependency is missing, ping me with the error and I'll fix
  `requirements.txt`

**"I want to keep the Space private but share it with someone"**
- HF Settings → your Space → **Settings** tab → **Collaborators**
- Add their HF username

**"Free tier sleeping"**
- The free CPU Space sleeps after 48 hr of inactivity
- Just open the URL once every 2 days to keep it awake
- Upgrade to **CPU upgrade ($0.03/hr)** or **persistent storage** when
  you want it always-on

## Notes on safety

- The Space is **paper-trading only** — no Upstox credentials live
  there, no real money can be moved
- Even when we wire Upstox real-time data (next session), the OAuth
  token never gets committed to the repo — it lives only in HF Space
  Secrets (similar to GitHub secrets)
- Your HF Space being **Private** means only you can see the URL until
  you choose to share
