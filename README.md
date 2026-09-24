# log-hub

Local FastAPI web hub for uploading and analyzing trading-bot logs.

## Local development

```powershell
pip install -r requirements.txt
$env:OPENAI_API_KEY = "your-key"
python app.py
```

Open <http://127.0.0.1:8765>.

## Deploy to Vercel

1. Import this GitHub repository into Vercel.
2. Open **Project → Settings → Environment Variables**.
3. Add `OPENAI_API_KEY` with the OpenAI secret key as its value.
4. Select Production, Preview, and Development as needed.
5. Optionally add `OPENAI_MODEL` (defaults to `gpt-5.6-sol`).
6. Redeploy after changing environment variables.

The OpenAI key is read only by the Python backend. It is not sent to or
stored by the browser.

### Serverless runtime note

Vercel storage is temporary. Uploaded logs and generated summaries can
disappear between requests or deployments. The Coin and Canonical analyzers
also depend on scripts from their original local project folders, so those
analyzers require those scripts to be moved into this repository before they
can run on Vercel. SuperMA and the OpenAI narrative service do not have that
external-script dependency.
