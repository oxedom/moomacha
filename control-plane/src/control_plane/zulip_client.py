import json

import httpx


class ZulipClient:
    """Thin async wrapper over Zulip REST API calls."""

    def __init__(self, site: str, email: str, api_key: str) -> None:
        self._base = site.rstrip("/")
        self._auth = (email, api_key)

    async def add_reaction(self, message_id: int, emoji_name: str) -> None:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/messages/{message_id}/reactions",
                data={"emoji_name": emoji_name},
            )
            resp.raise_for_status()

    async def send_message(self, channel: str, topic: str, content: str) -> int:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/messages",
                data={"type": "stream", "to": channel, "topic": topic, "content": content},
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def send_direct_message(self, recipient_ids: list[int], content: str) -> int:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/messages",
                data={
                    "type": "direct",
                    "to": json.dumps(recipient_ids),
                    "content": content,
                },
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def upload_file(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/user_uploads",
                files={"filename": (filename, content, content_type)},
            )
            resp.raise_for_status()
            return resp.json()

    async def update_message(self, message_id: int, content: str) -> None:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.patch(
                f"{self._base}/api/v1/messages/{message_id}",
                data={"content": content},
            )
            resp.raise_for_status()

    async def _get_by_narrow(self, narrow: list[dict], num_before: int) -> list[dict]:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.get(
                f"{self._base}/api/v1/messages",
                params={
                    "anchor": "newest",
                    "num_before": num_before,
                    "num_after": 0,
                    "narrow": json.dumps(narrow),
                    "apply_markdown": "false",
                },
            )
            resp.raise_for_status()
            return resp.json()["messages"]

    async def get_messages(self, channel: str, topic: str, num_before: int) -> list[dict]:
        narrow = [
            {"operator": "stream", "operand": channel},
            {"operator": "topic", "operand": topic},
        ]
        return await self._get_by_narrow(narrow, num_before)

    async def get_channel_messages(self, channel: str, num_before: int) -> list[dict]:
        narrow = [{"operator": "stream", "operand": channel}]
        return await self._get_by_narrow(narrow, num_before)

    async def get_direct_messages(self, recipient_ids: list[int], num_before: int) -> list[dict]:
        narrow = [{"operator": "dm", "operand": recipient_ids}]
        return await self._get_by_narrow(narrow, num_before)

    async def subscribe_to_channel(self, channel: str) -> None:
        async with httpx.AsyncClient(auth=self._auth) as http:
            resp = await http.post(
                f"{self._base}/api/v1/users/me/subscriptions",
                data={"subscriptions": json.dumps([{"name": channel}])},
            )
            resp.raise_for_status()
