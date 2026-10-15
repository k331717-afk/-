"""Safety gates for official attachment parsing before live authorization."""
import sys
import types
import unittest
from unittest.mock import patch

if "requests" not in sys.modules:
    try:
        import requests
    except ImportError:
        sys.modules["requests"] = types.SimpleNamespace(get=None)

from review_openapi import parse_review_attachments, fetch_review_attachments


class ReviewOpenApiTests(unittest.TestCase):
    def response(self, **updates):
        value = {"reviewNo": 123, "siteCode": "S_test", "img": "https://example.com/a.jpg"}
        value.update(updates)
        return {"statusCode": 200, "data": value}

    def test_single_photo_and_json_multiple_photos(self):
        self.assertEqual(parse_review_attachments(self.response(), 123, "S_test"),
                         ["https://example.com/a.jpg"])
        self.assertEqual(parse_review_attachments(self.response(img='["https://example.com/a.jpg", "https://example.com/a.jpg", "https://example.com/b.jpg"]'), 123, "S_test"),
                         ["https://example.com/a.jpg", "https://example.com/b.jpg"])

    def test_other_review_or_store_is_rejected(self):
        for changes in ({"reviewNo": 124}, {"siteCode": "S_other"}):
            with self.assertRaises(ValueError):
                parse_review_attachments(self.response(**changes), 123, "S_test")

    def test_unknown_or_unsafe_media_is_rejected(self):
        for image in ("javascript:alert(1)", "relative.jpg", {"unknown": "url"}, "https://user:secret@example.com/a.jpg"):
            with self.assertRaises(ValueError):
                parse_review_attachments(self.response(img=image), 123, "S_test")

    def test_missing_auth_prevents_requests(self):
        with patch("review_openapi.requests.get") as get:
            with self.assertRaises(RuntimeError):
                fetch_review_attachments(123, "", "S_test")
            get.assert_not_called()

    def test_error_does_not_include_server_data_or_token(self):
        response = types.SimpleNamespace(status_code=403, text="private customer data")
        with patch("review_openapi.requests.get", return_value=response) as get:
            with self.assertRaises(RuntimeError) as raised:
                fetch_review_attachments(123, "secret-token", "S_test")
            self.assertNotIn("private", str(raised.exception))
            self.assertNotIn("secret-token", str(raised.exception))
            self.assertFalse(get.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
