import json

import pytest

from wallet.bootstrap import main

pytestmark = pytest.mark.database


def test_existing_admin_requires_explicit_token_issuance(api, monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap"])
    with pytest.raises(SystemExit, match="--issue-token"):
        main()
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM auth_tokens").fetchone()[0] == 1


def test_issue_token_preserves_existing_admin_access(api, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["bootstrap", "--issue-token", "--json"])
    main()
    credential = json.loads(capsys.readouterr().out.splitlines()[-1])
    for token in (api.admin_token, credential["token"]):
        response = api.request("GET", f"/users/{credential['id']}/wallets", token=token)
        assert response.status_code == 200
    with api.owner() as conn:
        assert conn.execute("SELECT count(*) FROM auth_tokens").fetchone()[0] == 2
