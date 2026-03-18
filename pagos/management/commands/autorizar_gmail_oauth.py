from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Autoriza Gmail OAuth (send+modify) y guarda tokens de envío e inbox."

    def handle(self, *args, **options):
        client_file = Path(settings.GMAIL_OAUTH_CLIENT_FILE)
        send_token_file = Path(settings.GMAIL_OAUTH_TOKEN_FILE)
        inbox_token_file = Path(getattr(settings, "GMAIL_OAUTH_INBOX_TOKEN_FILE", settings.GMAIL_OAUTH_TOKEN_FILE))
        if not client_file.exists():
            raise CommandError(f"No existe client file: {client_file}")

        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_file),
            scopes=[
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )
        creds = flow.run_local_server(port=0, open_browser=False)

        for token_file in {send_token_file, inbox_token_file}:
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
            token_file.chmod(0o600)

        self.stdout.write(self.style.SUCCESS(f"Token OAuth (send) guardado en {send_token_file}"))
        self.stdout.write(self.style.SUCCESS(f"Token OAuth (inbox) guardado en {inbox_token_file}"))
        self.stdout.write(f"Scopes concedidos: {', '.join(creds.scopes or [])}")
