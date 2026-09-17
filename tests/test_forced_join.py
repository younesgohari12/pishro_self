from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from services.membership_service import check_required_memberships


class UserNotParticipantError(Exception):
    pass


class FakeBot:
    def __init__(self, permissions=None, entity_error=None):
        self.permissions = permissions or {}
        self.entity_error = entity_error

    async def get_entity(self, source):
        if self.entity_error:
            raise self.entity_error
        key = str(source).lstrip('@')
        return SimpleNamespace(title=f'Channel {key}', username=key)

    async def get_permissions(self, entity, user_id):
        value = self.permissions.get(entity.username, SimpleNamespace(has_left=False, is_banned=False))
        if isinstance(value, Exception):
            raise value
        return value


class ForcedJoinTest(unittest.IsolatedAsyncioTestCase):
    async def test_member_passes(self):
        joined, missing = await check_required_memberships(FakeBot(), 100, ['@required'])
        self.assertTrue(joined)
        self.assertEqual(missing, [])

    async def test_non_member_is_blocked_with_join_url(self):
        bot = FakeBot({'required': UserNotParticipantError('not joined')})
        joined, missing = await check_required_memberships(bot, 100, ['@required'])
        self.assertFalse(joined)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].reason, 'not_member')
        self.assertEqual(missing[0].url, 'https://t.me/required')

    async def test_left_or_banned_user_is_blocked(self):
        for permission in (
            SimpleNamespace(has_left=True, is_banned=False),
            SimpleNamespace(has_left=False, is_banned=True),
        ):
            joined, missing = await check_required_memberships(
                FakeBot({'required': permission}), 100, ['@required']
            )
            self.assertFalse(joined)
            self.assertEqual(len(missing), 1)

    async def test_restricted_but_present_member_passes(self):
        ChannelParticipantBanned = type('ChannelParticipantBanned', (), {})
        participant = ChannelParticipantBanned()
        participant.left = False
        permission = SimpleNamespace(
            has_left=False, is_banned=True, participant=participant
        )
        joined, missing = await check_required_memberships(
            FakeBot({'required': permission}), 100, ['@required']
        )
        self.assertTrue(joined)
        self.assertEqual(missing, [])

    async def test_resolution_error_is_fail_closed(self):
        joined, missing = await check_required_memberships(
            FakeBot(entity_error=ValueError('unknown channel')), 100, ['@missing']
        )
        self.assertFalse(joined)
        self.assertEqual(missing[0].reason, 'check_failed')

    async def test_all_required_channels_must_pass(self):
        bot = FakeBot({'two': UserNotParticipantError('not joined')})
        joined, missing = await check_required_memberships(bot, 100, ['@one', '@two'])
        self.assertFalse(joined)
        self.assertEqual([item.title for item in missing], ['Channel two'])


class AdminGameButtonTest(unittest.TestCase):
    def test_group_button_exists_in_new_and_legacy_admin_panels(self):
        root = Path(__file__).parents[1]
        new_panel = (root / 'handlers' / 'admin.py').read_text(encoding='utf-8')
        legacy_panel = (root / 'bot' / 'core.py').read_text(encoding='utf-8')
        for source in (new_panel, legacy_panel):
            self.assertIn('📍 گروه شرط‌بندی', source)
            self.assertIn('ag_group', source)
        self.assertNotIn('await bot.get_participant(', legacy_panel)


if __name__ == '__main__':
    unittest.main()
