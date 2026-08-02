# SanctionScreen — Streamlit Cloud deploy branch

Contains only the demo app and requirements.txt so Streamlit Community
Cloud uses pip/uv with requirements.txt (on main, the platform prefers
uv.lock, which omits the demo dependencies). The sanctionscreen package
itself installs from the main branch via the git reference in
requirements.txt. Sync demo/app.py + requirements.txt from main when they
change.
