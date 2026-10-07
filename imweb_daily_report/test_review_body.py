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
    requests.RequestException = type('RequestException', (Exception,), {})
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
from review_media import parse_review_images
from review_sync import ensure_review_images, collect_missing_images


def stored(blocks):
    return [dict(block, id=f'block-{i}') for i, block in enumerate(blocks)]


class ReviewBodyTests(unittest.TestCase):
    def test_native_review_attachment_excludes_product_and_avatar(self):
        html = '''<div class="modal-left"><div class="_block_no_img"><img src="https://example.com/product.jpg"></div>
            <div class="item _review_img"><img src="https://example.com/first.jpg"></div>
            <div class="item _review_img"><img src="https://example.com/second.jpg"/></div></div>
            <div class="modal-right"><img src="https://example.com/avatar.jpg"><span class="txt _review_body">Review</span></div>'''
        self.assertEqual(parse_review_images({'msg':'SUCCESS','idx':123,'html':html}, '123'),
                         ['https://example.com/first.jpg','https://example.com/second.jpg'])

    def test_public_review_identity_and_structure_are_required(self):
        for payload in [{'msg':'SUCCESS','idx':2,'html':'<div class="_review_body">X</div>'},
                        {'msg':'SUCCESS','idx':1,'html':'<img src="https://example.com/product.jpg">'},
                        {'msg':'ERROR','idx':1}]:
            with self.assertRaises(RuntimeError):
                parse_review_images(payload, '1')

    def test_public_html_images_deduplicate_and_reject_invalid_scheme(self):
        html = '<div class="_review_body"><br><img src="https://example.com/a.jpg?a=1&amp;b=2"><img src="javascript:x"><img src="https://example.com/a.jpg?a=1&amp;b=2"></div>'
        self.assertEqual(parse_review_images({'msg':'SUCCESS','idx':1,'html':html},'1'), ['https://example.com/a.jpg?a=1&b=2'])

    def test_crlf_normalization_prevents_false_write_failure(self):
        body, images = review_content('첫 줄\r\n둘째 줄\r\n')
        self.assertEqual(body, '첫 줄\n둘째 줄\n')
        client = Mock()
        client._request.return_value = {'results': stored(body_blocks(body, images))}
        ensure_body(client, 'page', body, images)
        self.assertEqual(client._request.call_count, 1)

    def test_missing_native_photo_is_fetched_and_failed_photo_retries(self):
        review = {'idx':123, 'is_photo':True}
        with patch('review_sync.fetch_review_images', return_value=['https://example.com/a.jpg']) as fetch:
            self.assertEqual(collect_missing_images(review, []), (['https://example.com/a.jpg'], False))
            fetch.assert_called_once_with('123')
        with patch('review_sync.fetch_review_images', return_value=[]):
            self.assertEqual(collect_missing_images(review, []), ([], True))

    def test_private_review_is_not_requested_publicly(self):
        with patch('review_sync.fetch_review_images') as fetch:
            self.assertEqual(collect_missing_images({'idx':1,'is_photo':True,'is_secret':True}, []), ([], True))
            fetch.assert_not_called()

    def test_sync_photo_repair_preserves_user_edits_and_is_idempotent(self):
        client = Mock()
        original = stored(body_blocks('사용자가 편집한 내용', ['https://example.com/a.jpg']))
        client._request.side_effect = [{'results':original}, {'results':body_blocks('', ['https://example.com/b.jpg'])[2:]}]
        self.assertEqual(ensure_review_images(client, 'page', ['https://example.com/a.jpg','https://example.com/b.jpg']), 1)
        children = client._request.call_args.kwargs['json']['children']
        self.assertEqual([b['type'] for b in children], ['image'])
        client._request.side_effect = None
        client._request.return_value = {'results':original + children}
        self.assertEqual(ensure_review_images(client, 'page', ['https://example.com/a.jpg','https://example.com/b.jpg']), 0)

    def test_photo_append_response_is_verified(self):
        client = Mock()
        client._request.side_effect = [{'results':stored(body_blocks('리뷰', []))}, {'results':[]}]
        with self.assertRaises(RuntimeError):
            ensure_review_images(client, 'page', ['https://example.com/a.jpg'])

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
