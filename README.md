# Startup Trainer — External Pilot v1

Private founder-facing pilot. Not an investment decision.

## Env
- OPENAI_API_KEY
- OPENAI_MODEL (default gpt-5.6)
- INVITE_CODE
- ADMIN_KEY

## Run
`pip install -r requirements.txt`
`python app.py`

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
