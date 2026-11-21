"""Read Google's downloaded Web OAuth JSON without copying secrets into Git."""
import json
from pathlib import Path


def load_google_config(credentials_path, calendar_id, origin):
    path = Path(credentials_path).resolve()
    repository = Path(__file__).resolve().parents[2]
    if path.is_relative_to(repository):
        raise ValueError('Keep Google credentials outside the project folder.')
    if (not isinstance(calendar_id, str) or not calendar_id.strip() or len(calendar_id) > 250 or
            calendar_id.strip().lower() == 'primary' or any(c.isspace() for c in calendar_id)):
        raise ValueError('Use the ID of a dedicated secondary Google test calendar.')
    raw = path.read_bytes()
    if len(raw) > 65536: raise ValueError('Invalid OAuth file size.')
    data = json.loads(raw)
    web = data.get('web') if isinstance(data, dict) else None
    if not isinstance(web, dict): raise ValueError('Choose Web application credentials, not Desktop app.')
    callback = origin+'/api/student/google/callback'
    redirects = web.get('redirect_uris')
    if not isinstance(redirects, list) or callback not in redirects:
        raise ValueError('Add the exact STAIR callback URL to the OAuth Web client.')
    client, secret = web.get('client_id'), web.get('client_secret')
    if (not isinstance(client, str) or not client.endswith('.apps.googleusercontent.com') or len(client) > 500 or
            not isinstance(secret, str) or not 1 <= len(secret) <= 1024 or
            any(c.isspace() for c in client+secret)):
        raise ValueError('The Web OAuth credentials are incomplete or invalid.')
    return {'STAIR_GOOGLE_CLIENT_ID': client, 'STAIR_GOOGLE_CLIENT_SECRET': secret,
            'STAIR_GOOGLE_TEST_CALENDAR_ID': calendar_id}
