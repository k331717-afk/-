import sys
import types
import unittest
from unittest.mock import Mock, patch

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
from migrate_review_photos import ensure_photos, property_images


def stored(blocks):
    return [dict(block, id=f'block-{i}') for i, block in enumerate(blocks)]


class ReviewBodyTests(unittest.TestCase):
    def test_removed_properties_and_low_rating(self):
        props = review_properties({'idx': 1, 'body': '좋아요', 'rating': 3}, '상의', '2026-10-01T00:00:00+09:00')
        self.assertFalse({'내용', '리뷰 번호', '비밀글', '숨김', '사진'} & props.keys())
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
        with patch.dict('os.environ', {'REVIEW_NOTION_TOKEN': 'test-only-never-an-api-credential'}):
            encoded = encode_state(state)
            self.assertNotIn('page_id', encoded)
            self.assertEqual(decode_state(encoded), state)
        with patch.dict('os.environ', {'REVIEW_NOTION_TOKEN': 'different-test-key'}):
            with self.assertRaises(Exception):
                decode_state(encoded)

    def test_known_markdown_import_restores_exact_source(self):
        client = Mock()
        source = '잘 맞아요\\~\\~ \n다음 줄'
        client._request.side_effect = [
            {'results': stored(body_blocks('잘 맞아요~~\n다음 줄', []))},
            stored(body_blocks(source, []))[1],
        ]
        ensure_body(client, 'page', source, [], preserve_existing=True, allow_format_repair=True)
        self.assertEqual(client._request.call_args.kwargs['json']['paragraph']['rich_text'][0]['text']['content'], source)

    def test_format_repair_rejects_changed_words(self):
        client = Mock()
        client._request.return_value = {'results': stored(body_blocks('사용자가 고친 내용', []))}
        with self.assertRaises(RuntimeError):
            ensure_body(client, 'page', '원래 내용', [], preserve_existing=True, allow_format_repair=True)
        self.assertEqual(client._request.call_count, 1)

    def test_photo_move_preserves_body_and_existing_image(self):
        client = Mock()
        client._request.side_effect = [
            {'results': stored(body_blocks('사용자가 편집한 리뷰', ['https://example.com/one.jpg']))},
            {'results': body_blocks('', ['https://example.com/two.jpg'])[2:]},
        ]
        self.assertEqual(ensure_photos(client, 'page', ['https://example.com/one.jpg', 'https://example.com/two.jpg']), 1)
        write = client._request.call_args.kwargs['json']
        self.assertEqual([b['type'] for b in write['children']], ['image'])
        self.assertEqual(write['children'][0]['image']['external']['url'], 'https://example.com/two.jpg')

    def test_photo_retry_is_idempotent(self):
        client = Mock()
        client._request.return_value = {'results': stored(body_blocks('리뷰', ['https://example.com/one.jpg']))}
        self.assertEqual(ensure_photos(client, 'page', ['https://example.com/one.jpg']), 0)
        self.assertEqual(client._request.call_count, 1)

    def test_photo_move_rejects_expiring_url(self):
        page = {'properties': {'사진': {'files': [{'type': 'file', 'file': {'url': 'https://example.com/temporary'}}]}}}
        with self.assertRaises(RuntimeError):
            property_images(page)

    def test_photo_response_must_contain_saved_image(self):
        client = Mock()
        client._request.side_effect = [{'results': stored(body_blocks('리뷰', []))}, {'results': []}]
        with self.assertRaises(RuntimeError):
            ensure_photos(client, 'page', ['https://example.com/one.jpg'])

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
