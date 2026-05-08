from __future__ import annotations

from backend.App.shared.infrastructure.message_formatting import to_anthropic_messages


def test_to_anthropic_messages_translates_openai_image_parts():
    system, messages = to_anthropic_messages(
        [
            {"role": "system", "content": "System prompt"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,aGVsbG8=",
                            "detail": "high",
                        },
                    },
                ],
            },
        ]
    )

    assert system == "System prompt"
    assert messages[0]["content"][0] == {"type": "text", "text": "Look at this."}
    assert messages[0]["content"][1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "aGVsbG8=",
        },
    }
