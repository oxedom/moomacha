from control_plane.models import OutgoingWebhookPayload


SAMPLE = {
    "bot_email": "echo-bot@example.zulipchat.com",
    "bot_full_name": "Echo",
    "data": "@**Echo** hello world",
    "trigger": "mention",
    "token": "tok123",
    "message": {
        "id": 112,
        "content": "@**Echo** hello world",
        "display_recipient": "sandbox",
        "subject": "greetings",
        "stream_id": 123456,
        "sender_id": 5,
        "sender_email": "iago@example.zulipchat.com",
        "type": "stream",
        "timestamp": 1527876931,
    },
}


def test_parse_payload_maps_fields():
    p = OutgoingWebhookPayload.model_validate(SAMPLE)

    assert p.token == "tok123"
    assert p.message.id == 112
    assert p.message.content == "@**Echo** hello world"
    assert p.message.display_recipient == "sandbox"
    assert p.message.subject == "greetings"


def test_parse_payload_ignores_unknown_fields():
    extra = {**SAMPLE, "unexpected": "ok", "message": {**SAMPLE["message"], "reactions": []}}
    p = OutgoingWebhookPayload.model_validate(extra)

    assert p.message.id == 112
