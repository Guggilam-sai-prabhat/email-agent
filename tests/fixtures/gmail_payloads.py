"""Sanitized fake Gmail API payloads for tests. No real addresses or content."""

PLAIN_TEXT_MESSAGE = {
    "id": "fake-msg-1",
    "threadId": "fake-thread-1",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "Sender Example <sender@example.test>"},
            {"name": "To", "value": "recipient@example.test, second@example.test"},
            {"name": "Subject", "value": "Quarterly update"},
            {"name": "Date", "value": "Tue, 01 Jul 2025 10:00:00 +0000"},
        ],
        "body": {"data": "SGVsbG8gdGVhbSwgaGVyZSBpcyB0aGUgdXBkYXRlLg=="},  # "Hello team, here is the update."
    },
}

MULTIPART_MESSAGE = {
    "id": "fake-msg-2",
    "threadId": "fake-thread-2",
    "payload": {
        "mimeType": "multipart/alternative",
        "headers": [
            {"name": "From", "value": "sender@example.test"},
            {"name": "To", "value": "recipient@example.test"},
            {"name": "Subject", "value": "Multipart note"},
            {"name": "Date", "value": "Wed, 02 Jul 2025 09:30:00 +0000"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": "UGxhaW4gdGV4dCBib2R5Lg=="},  # "Plain text body."
            },
            {
                "mimeType": "text/html",
                "body": {"data": "PGh0bWw+PGJvZHk+PHA-SFRNTCBib2R5LjwvcD48L2JvZHk+PC9odG1sPg=="},
            },
        ],
    },
}

HTML_ONLY_MESSAGE = {
    "id": "fake-msg-3",
    "threadId": "fake-thread-3",
    "payload": {
        "mimeType": "text/html",
        "headers": [
            {"name": "From", "value": "sender@example.test"},
            {"name": "To", "value": "recipient@example.test"},
            {"name": "Subject", "value": "HTML only"},
            {"name": "Date", "value": "Thu, 03 Jul 2025 08:00:00 +0000"},
        ],
        # "<html><body><p>Hello <b>world</b></p><script>evil()</script></body></html>"
        "body": {
            "data": "PGh0bWw-PGJvZHk-PHA-SGVsbG8gPGI-d29ybGQ8L2I-PC9wPjxzY3JpcHQ-ZXZpbCgpPC9zY3JpcHQ-PC9ib2R5Pjwv"
            "aHRtbD4="
        },
    },
}

EMPTY_BODY_MESSAGE = {
    "id": "fake-msg-4",
    "threadId": "fake-thread-4",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "sender@example.test"},
            {"name": "To", "value": "recipient@example.test"},
            {"name": "Subject", "value": "Empty body"},
            {"name": "Date", "value": "Fri, 04 Jul 2025 07:00:00 +0000"},
        ],
        "body": {},
    },
}

MISSING_HEADERS_MESSAGE = {
    "id": "fake-msg-5",
    "threadId": "fake-thread-5",
    "payload": {
        "mimeType": "text/plain",
        "headers": [],
        "body": {"data": "Tm8gaGVhZGVycyBoZXJlLg=="},  # "No headers here."
    },
}

MESSAGE_WITH_ATTACHMENT = {
    "id": "fake-msg-6",
    "threadId": "fake-thread-6",
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "From", "value": "sender@example.test"},
            {"name": "To", "value": "recipient@example.test"},
            {"name": "Subject", "value": "Has an attachment"},
            {"name": "Date", "value": "Sat, 05 Jul 2025 06:00:00 +0000"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": "Qm9keSB0ZXh0Lg=="},  # "Body text."
            },
            {
                "mimeType": "application/pdf",
                "filename": "fake-report.pdf",
                "body": {"attachmentId": "fake-attachment-id", "size": 1234},
            },
        ],
    },
}

AUTOMATED_MESSAGE = {
    "id": "fake-msg-7",
    "threadId": "fake-thread-7",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "no-reply@example.test"},
            {"name": "To", "value": "recipient@example.test"},
            {"name": "Subject", "value": "Automated notification"},
            {"name": "Date", "value": "Sun, 06 Jul 2025 05:00:00 +0000"},
            {"name": "Auto-Submitted", "value": "auto-generated"},
        ],
        "body": {"data": "VGhpcyBpcyBhbiBhdXRvbWF0ZWQgbWVzc2FnZS4="},  # "This is an automated message."
    },
}
