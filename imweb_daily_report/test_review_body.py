import sys
import types
import unittest
from unittest.mock import Mock

try:
    import requests
except ModuleNotFoundError:
    requests = types.ModuleType('requests')
    requests.Timeout = type('Timeout', (Exception,), {})
    requests.ConnectionError = type('ConnectionError', (Exception,), {})
    requests.HTTPError = type('HTTPError', (Exception,), {})
    sys.modules['requests'] = requests
try:
    import dotenv
except ModuleNotFoundError:
    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *args: None
    sys.modules['dotenv'] = dotenv

from review_sync import review_properties, review_content, body_blocks, ensure_body, existing_pages
from review_state import encode_state, decode_state


def stored(blocks):
    return [dict(block, id=f'block-{i}') for i, block in enumerate(blocks)]


class ReviewBodyTests(unittest.TestCase):
    def test_removed_properties_and_low_rating(self):
        props = review_properties({'idx': 1, 'body': '좋아요', 'rating': 3}, '상의', '2026-10-01T00:00:00+09:00')
        self.assertFalse({'내용', '리뷰 번호', '비밀글', '숨김'} & props.keys())
        self.assertTrue(props['리뷰']['title'][0]['text']['content'].startswith('🔴'))

    def test_html_text_and_images(self):
        text, images = review_content("<img src='https://example.com/a.jpg'><div>부드러워요<br>잘 맞아요 &amp; 예뻐요</div>")
        self.assertEqual(text, '부드러워요\n잘 맞아요 & 예뻐요')
        self.assertEqual(images, ['https://example.com/a.jpg'])

    def test_append_preserves_existing_notes(self):
        client = Mock()
        client._request.side_effect = [
            {'results': [{'id': 'note', 'type': 'paragraph', 'paragraph': {'rich_text': [{'text': {'content': '사용자 메모'}}]}}]},
            {'results': stored(body_blocks('원래 리뷰', []))},
        ]
        self.assertEqual(ensure_body(client, 'page', '원래 리뷰', [], preserve_existing=True), 'block-1')
        self.assertEqual(client._request.call_args.args[:2], ('PATCH', '/blocks/page/children'))

    def test_retry_does_not_duplicate_content(self):
        client = Mock()
        client._request.return_value = {'results': stored(body_blocks('본문\n둘째 줄', ['https://example.com/a.jpg']))}
        self.assertEqual(ensure_body(client, 'page', '본문\n둘째 줄', ['https://example.com/a.jpg'], preserve_existing=True), 'block-1')
        self.assertEqual(client._request.call_count, 1)

    def test_migration_stops_on_conflicting_body(self):
        client = Mock()
        client._request.return_value = {'results': stored(body_blocks('수정한 리뷰', []))}
        with self.assertRaises(RuntimeError):
            ensure_body(client, 'page', '열의 리뷰', [], preserve_existing=True)
        self.assertEqual(client._request.call_count, 1)

    def test_state_roundtrip(self):
        state = {'database_id': 'db', 'reviews': {'123': {'page_id': 'abc', 'images': []}}, 'pending': {}}
        self.assertEqual(decode_state(encode_state(state)), state)

    def test_lost_index_fails_closed(self):
        client = Mock(database_id='db')
        client._request.return_value = {'results': [{'id': 'existing', 'properties': {}}]}
        state = Mock(data={'reviews': {}, 'pending': {}})
        with self.assertRaises(RuntimeError):
            existing_pages(client, state)

    def test_pending_creation_recovers_without_recreating(self):
        from review_sync import property_fingerprint
        props = review_properties({'idx': 1, 'body': '좋아요', 'rating': 5}, '상의', '2026-10-01T00:00:00+09:00')
        client = Mock(database_id='db')
        client._request.return_value = {'results': [{'id': 'seed', 'properties': {}}, {'id': 'created', 'properties': props}]}
        state = Mock(data={'reviews': {'0': {'page_id': 'seed'}}, 'pending': {'1': {'fingerprint': property_fingerprint(props), 'body_hash': 'digest', 'images': []}}})
        known = existing_pages(client, state)
        self.assertEqual(known['1']['id'], 'created')
        self.assertFalse(state.data['pending'])
        self.assertEqual(client._request.call_count, 1)


if __name__ == '__main__':
    unittest.main()

