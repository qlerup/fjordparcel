from unittest.mock import Mock
import pytest
import parcel_map_image as images


@pytest.fixture
def cache(tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, 'DATABASE_PATH', str(tmp_path/'test.db'))
    data = dict(points=[dict(name='Køge',lat=55.45802,lon=12.18214)], segments=[], pending=False)
    monkeypatch.setattr(images, 'route_overview', lambda events: data)
    capture = Mock(return_value=b'png')
    monkeypatch.setattr(images, '_screenshot', capture)
    return data, capture


def test_same_route_data_is_shared_and_image_cached(cache):
    data, capture = cache
    assert images.render_map_image([]) == b'png'
    capture.assert_called_once_with(data)
    assert images.render_map_image([]) == b'png'
    assert capture.call_count == 1
    data['points'].append(dict(name='Aarhus',lat=56.15,lon=10.2))
    images.render_map_image([])
    assert capture.call_count == 2


def test_missing_points_do_not_generate_a_fake_image(cache):
    data, capture = cache
    data['points'] = []
    assert images.render_map_image([]) is None
    capture.assert_not_called()


def test_pending_road_route_retries_instead_of_sending_different_map(cache,monkeypatch):
    data, capture = cache
    data['pending'] = True
    monkeypatch.setattr(images.time, 'monotonic', Mock(side_effect=[0,9]))
    with pytest.raises(RuntimeError,match='still being calculated'):
        images.render_map_image([])
    capture.assert_not_called()


def test_browser_failure_is_not_cached_and_can_be_retried(cache):
    data, capture = cache
    capture.side_effect = [RuntimeError('tiles unavailable'), b'correct image']
    with pytest.raises(RuntimeError):
        images.render_map_image([])
    assert images.render_map_image([]) == b'correct image'
    assert capture.call_count == 2
