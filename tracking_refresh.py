"""Five-minute tracking polling, isolated from potentially slow mail scans."""
from datetime import datetime, timezone
import logging
import time
import uuid

import storage

INTERVAL_SECONDS = 5 * 60
LOCK_NAME = 'tracking-refresh'
OWNER = uuid.uuid4().hex
LEASE_SECONDS = 60
log = logging.getLogger(__name__)


def is_due(shipment, now):
    try:
        checked = datetime.fromisoformat(str(shipment.get('last_checked_at') or '').replace('Z', '+00:00'))
        checked = checked.replace(tzinfo=checked.tzinfo or timezone.utc).timestamp()
        return now - checked >= INTERVAL_SECONDS
    except (ValueError, TypeError):
        return True


def refresh_due_shipments(now=None):
    now = time.time() if now is None else now
    # Archived parcels do not need continuous API polling.
    if not any(is_due(s, now) for s in storage.list_shipments()):
        return 0
    lock = dict(lock_name=LOCK_NAME, lease_seconds=LEASE_SECONDS)
    if not storage.try_acquire_automation_leader_lock(OWNER, **lock):
        return 0
    count = 0
    try:
        # Re-read after acquiring the lock: another worker may have just polled.
        for shipment in storage.list_shipments():
            if not is_due(shipment, now):
                continue
            if not storage.renew_automation_leader_lock(OWNER, **lock):
                break
            try:
                storage.refresh_shipment_tracking(shipment['id'])
                count += 1
            except Exception:
                log.exception('Automatic tracking lookup failed for shipment %s', shipment['id'])
    finally:
        storage.release_automation_leader_lock(OWNER, lock_name=LOCK_NAME)
    if count:
        log.info('Tracking refresh completed: %s parcel(s)', count)
    return count
