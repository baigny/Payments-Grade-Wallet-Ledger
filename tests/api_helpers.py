import asyncio
from dataclasses import dataclass
from uuid import uuid4

import httpx

from wallet.main import app


@dataclass
class API:
    admin_token: str
    owner: object

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def request(self, method, path, *, token=None, key="auto", **kwargs):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if method == "POST" and key is not None:
            headers["Idempotency-Key"] = str(uuid4()) if key == "auto" else key

        async def perform():
            async with self.client() as client:
                return await client.request(method, path, headers=headers, **kwargs)

        return asyncio.run(perform())

    def user(self, name="Nila"):
        response = self.request(
            "POST",
            "/users",
            token=self.admin_token,
            json={
                "name": name,
                "email": f"{uuid4().hex}@example.test",
                "phone": "+91" + str(uuid4().int % 10**10).zfill(10),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def wallet(self, user, name="Main"):
        response = self.request(
            "POST", f"/users/{user['id']}/wallets", token=user["token"], json={"name": name}
        )
        assert response.status_code == 201, response.text
        return response.json()
