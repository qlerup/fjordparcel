from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
import tracking_refresh as refresh

NOW = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc).timestamp()


def shipment(ident=1, checked='2026-11-09T12:50:00Z'):
    return dict(id=ident, last_checked_at=checked)


@pytest.fixture
def deps(monkeypatch):
    calls = {}
    for name, value in [('list_shipments', [shipment()]), ('try_acquire_automation_leader_lock', True),
                        ('renew_automation_leader_lock', True), ('release_automation_leader_lock', True),
                        ('refresh_shipment_tracking', None)]:
        calls[name] = Mock(return_value=value)
        monkeypatch.setattr(refresh.storage, name, calls[name])
    return calls


@pytest.mark.parametrize('checked,due', [('2026-11-09T12:55:00Z', True),
    ('2026-11-09T13:55:01+01:00', False), ('2026-11-09T12:59:59Z', False), ('', True), ('invalid', True)])
def test_five_minute_interval_uses_persisted_check_time(checked,due):
    assert refresh.is_due(shipment(checked=checked), NOW) is due


def test_only_due_active_parcels_are_polled(deps):
    deps['list_shipments'].return_value = [shipment(), shipment(2, '2026-11-09T12:59:00Z')]
    assert refresh.refresh_due_shipments(NOW) == 1
    deps['refresh_shipment_tracking'].assert_called_once_with(1)
    assert all(call.args == () and call.kwargs == {} for call in deps['list_shipments'].call_args_list)
    assert deps['try_acquire_automation_leader_lock'].call_args.kwargs['lock_name'] == 'tracking-refresh'
    deps['release_automation_leader_lock'].assert_called_once()


def test_recent_manual_check_avoids_unnecessary_api_call(deps):
    deps['list_shipments'].return_value = [shipment(checked='2026-11-09T12:59:00Z')]
    assert refresh.refresh_due_shipments(NOW) == 0
    deps['try_acquire_automation_leader_lock'].assert_not_called()
    deps['refresh_shipment_tracking'].assert_not_called()


def test_other_worker_owns_lock_or_already_polled(deps):
    deps['try_acquire_automation_leader_lock'].return_value = False
    assert refresh.refresh_due_shipments(NOW) == 0
    deps['refresh_shipment_tracking'].assert_not_called()
    deps['try_acquire_automation_leader_lock'].return_value = True
    deps['list_shipments'].side_effect = [[shipment()], [shipment(checked='2026-11-09T12:59:00Z')]]
    assert refresh.refresh_due_shipments(NOW) == 0
    deps['refresh_shipment_tracking'].assert_not_called()


def test_one_failure_does_not_block_others_and_lock_is_released(deps):
    deps['list_shipments'].return_value = [shipment(1), shipment(2)]
    deps['refresh_shipment_tracking'].side_effect = [RuntimeError('offline'), None]
    assert refresh.refresh_due_shipments(NOW) == 1
    assert deps['refresh_shipment_tracking'].call_count == 2
    deps['release_automation_leader_lock'].assert_called_once()


def test_lost_lease_stops_polling(deps):
    deps['renew_automation_leader_lock'].return_value = False
    assert refresh.refresh_due_shipments(NOW) == 0
    deps['refresh_shipment_tracking'].assert_not_called()
    deps['release_automation_leader_lock'].assert_called_once()
