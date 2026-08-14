"""V1.7 kullanıcı deposunu kripto/Telegram reposundan fiziksel olarak ayırır.

Bu giriş noktası mevcut dashboard_accounts_app davranışını korur ancak dinamik
kullanıcıların saklandığı GitHub reposunu ayrı bir ortam değişkeninden alır.
Böylece kullanıcı-yazma tokeni kriptosinyalanaliz63 reposuna hiç yetki almak
zorunda değildir.
"""

from __future__ import annotations

import os

import dashboard_accounts_app as app


USERS_REPOSITORY_ENV = "PANEL_USERS_REPOSITORY"


def isolated_account_store_from_env(config: app.PanelConfig) -> app.GitHubAccountStore:
    repository = (os.getenv(USERS_REPOSITORY_ENV) or "").strip()
    if not repository or "/" not in repository:
        raise RuntimeError(
            "PANEL_USERS_REPOSITORY owner/repo biçiminde ayrı private kullanıcı reposunu göstermelidir."
        )
    if repository.casefold() == str(config.repository or "").casefold():
        raise RuntimeError(
            "Güvenlik için PANEL_USERS_REPOSITORY sinyal/Telegram reposundan farklı olmalıdır."
        )
    return app.GitHubAccountStore(
        repository,
        os.getenv("GITHUB_PANEL_USERS_TOKEN"),
        ref=os.getenv("PANEL_USERS_REF", "main"),
        path=os.getenv("PANEL_USERS_PATH", app.USERS_PATH_DEFAULT),
    )


# Mevcut V1.7 main fonksiyonu kendi global account_store_from_env çağrısını
# kullanır. Bu atama yalnız bu giriş noktasında onu izole sürümle değiştirir.
app.account_store_from_env = isolated_account_store_from_env


if __name__ == "__main__":
    app.main()
