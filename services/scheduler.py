"""Automatic Tabchi scheduler for active banners."""
from __future__ import annotations

import asyncio

from telethon import errors

import self_manager
from config import TABCHI_SCHEDULER_INTERVAL, TABCHI_SEND_DELAY_SECONDS
from database import models
from services.access_service import can_run
from services.sender import collect_banner_targets, send_banner_to_target, DeliverySkipped
from services.logging_service import get_logger, log_api_error

logger = get_logger('scheduler')


async def run_scheduler():
    print('✅ زمان‌بند تبچی فعال شد')
    logger.info('Tabchi scheduler started')
    while True:
        try:
            due = models.list_due_banners(limit=50)
            for banner in due:
                uid = int(banner['user_id'])
                bid = int(banner['id'])
                if not can_run(uid):
                    models.defer_banner(bid, uid, 60)
                    continue
                client = self_manager.get_client_for_user(uid)
                if client is None or not client.is_connected():
                    models.defer_banner(bid, uid, 60)
                    continue

                try:
                    targets = await collect_banner_targets(client, banner, uid)
                except Exception as exc:
                    models.log_send(
                        banner_id=bid, user_id=uid, target_chat_id=None,
                        target_title='target-discovery', status='error',
                        error=f'{type(exc).__name__}: {exc}',
                    )
                    models.defer_banner(bid, uid, 60)
                    continue

                # No eligible dialogs is still a completed cycle; retry at the
                # banner's configured interval instead of hammering the account.
                if not targets:
                    models.mark_banner_cycle(bid, uid, banner['interval'])
                    continue

                interrupted = False
                for target in targets:
                    if not can_run(uid):
                        models.defer_banner(bid, uid, 60)
                        interrupted = True
                        break
                    if target.get('blacklisted'):
                        models.log_send(
                            banner_id=bid, user_id=uid,
                            target_chat_id=target['chat_id'], target_title=target['title'],
                            status='skipped_blacklist',
                        )
                        continue

                    try:
                        sent = await send_banner_to_target(client, banner, target)
                        msg_id = getattr(sent, 'id', None)
                        if isinstance(sent, list) and sent:
                            msg_id = getattr(sent[0], 'id', None)
                        models.log_send(
                            banner_id=bid, user_id=uid,
                            target_chat_id=target['chat_id'], target_title=target['title'],
                            status='sent', telegram_message_id=msg_id,
                        )
                    except DeliverySkipped:
                        models.log_send(banner_id=bid,user_id=uid,target_chat_id=target['chat_id'],
                                        target_title=target['title'],status='skipped_policy')
                    except errors.FloodWaitError as exc:
                        models.log_send(
                            banner_id=bid, user_id=uid,
                            target_chat_id=target['chat_id'], target_title=target['title'],
                            status='flood_wait', error=f'FloodWait {exc.seconds}s',
                        )
                        models.defer_banner(bid, uid, max(int(exc.seconds), 60))
                        interrupted = True
                        break
                    except errors.PeerFloodError as exc:
                        # Telegram is explicitly rate-limiting unsolicited/mass
                        # behavior. Stop this banner instead of retrying it.
                        models.log_send(
                            banner_id=bid, user_id=uid,
                            target_chat_id=target['chat_id'], target_title=target['title'],
                            status='peer_flood', error=f'{type(exc).__name__}: {exc}',
                        )
                        models.set_banner_status(bid, uid, 'paused')
                        interrupted = True
                        break
                    except Exception as exc:
                        models.log_send(
                            banner_id=bid, user_id=uid,
                            target_chat_id=target['chat_id'], target_title=target['title'],
                            status='error', error=f'{type(exc).__name__}: {exc}',
                        )

                    await asyncio.sleep(max(0.2, float(TABCHI_SEND_DELAY_SECONDS)))

                if not interrupted:
                    models.mark_banner_cycle(bid, uid, banner['interval'])

            await asyncio.sleep(max(2, int(TABCHI_SCHEDULER_INTERVAL)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_api_error('tabchi_scheduler_loop', exc)
            await asyncio.sleep(10)
