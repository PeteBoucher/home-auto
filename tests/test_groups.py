"""Tests for the cross-integration device Groups feature."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.devices.models import Device, DeviceGroup, DeviceType, Integration
from app.services import groups as groups_service


@pytest.fixture(name="z2m_bulb2")
def z2m_bulb2_fixture(session):
    device = Device(
        name="Porch Light",
        device_id="porch_light",
        type=DeviceType.bulb,
        integration=Integration.zigbee2mqtt,
        online=True,
        state=False,
        dimmable=True,
        brightness=100,
        color_temp=50,
        color_mode="white",
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


class TestCreateGroup:
    def test_creates_group_and_assigns_members(self, session, z2m_bulb, tuya_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()) as mock_create, \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()) as mock_add:
            group = asyncio.run(groups_service.create_group(session, "Lounge & Dining", [z2m_bulb.id, tuya_bulb.id]))

        session.refresh(z2m_bulb)
        session.refresh(tuya_bulb)
        assert z2m_bulb.group_id == group.id
        assert tuya_bulb.group_id == group.id
        assert group.zigbee_group_name == f"group_{group.id}"
        mock_create.assert_awaited_once_with(group.zigbee_group_name)
        mock_add.assert_awaited_once_with(group.zigbee_group_name, z2m_bulb.device_id)

    def test_no_zigbee_group_for_tuya_only_group(self, session, tuya_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()) as mock_create:
            group = asyncio.run(groups_service.create_group(session, "Tuya only", [tuya_bulb.id]))
        assert group.zigbee_group_name is None
        mock_create.assert_not_awaited()


class TestSetGroupMembers:
    def test_adding_zigbee_member_registers_it(self, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id]))

        with patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()) as mock_add, \
             patch("app.services.groups.mqtt_client.remove_group_member", new=AsyncMock()) as mock_remove:
            asyncio.run(groups_service.set_group_members(session, group, [z2m_bulb.id, z2m_bulb2.id]))

        session.refresh(z2m_bulb2)
        assert z2m_bulb2.group_id == group.id
        mock_add.assert_awaited_once_with(group.zigbee_group_name, z2m_bulb2.device_id)
        mock_remove.assert_not_awaited()

    def test_removing_zigbee_member_unregisters_it(self, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, z2m_bulb2.id]))

        with patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.remove_group_member", new=AsyncMock()) as mock_remove:
            asyncio.run(groups_service.set_group_members(session, group, [z2m_bulb.id]))

        session.refresh(z2m_bulb2)
        assert z2m_bulb2.group_id is None
        mock_remove.assert_awaited_once_with(group.zigbee_group_name, z2m_bulb2.device_id)


class TestDeleteGroup:
    def test_clears_member_group_id_and_removes_zigbee_group(self, session, z2m_bulb, tuya_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, tuya_bulb.id]))

        with patch("app.services.groups.mqtt_client.remove_zigbee_group", new=AsyncMock()) as mock_remove:
            asyncio.run(groups_service.delete_group(session, group))

        session.refresh(z2m_bulb)
        session.refresh(tuya_bulb)
        assert z2m_bulb.group_id is None
        assert tuya_bulb.group_id is None
        mock_remove.assert_awaited_once()
        assert session.get(DeviceGroup, group.id) is None


class TestSendGroupCommand:
    def test_zigbee_members_get_one_groupcast(self, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, z2m_bulb2.id]))

        with patch("app.services.groups.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(groups_service.send_group_command(session, group, {"state": True}))

        mock_pub.assert_awaited_once_with(f"zigbee2mqtt/{group.zigbee_group_name}/set", {"state": "ON"})
        session.refresh(z2m_bulb)
        session.refresh(z2m_bulb2)
        session.refresh(group)
        assert z2m_bulb.state is True
        assert z2m_bulb2.state is True
        assert group.state is True

    def test_tuya_member_commanded_individually(self, session, tuya_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lamp group", [tuya_bulb.id]))

        with patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()) as mock_cmd, \
             patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value={
                 "online": True, "state": True, "brightness": 80,
                 "color_temp": 50, "color_mode": "white", "color_rgb": None,
             })):
            asyncio.run(groups_service.send_group_command(session, group, {"state": True}))

        mock_cmd.assert_awaited_once()
        session.refresh(tuya_bulb)
        assert tuya_bulb.state is True

    def test_color_mode_alone_does_not_publish(self, session, z2m_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id]))

        with patch("app.services.groups.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(groups_service.send_group_command(session, group, {"color_mode": "colour"}))

        mock_pub.assert_not_awaited()
        session.refresh(group)
        assert group.color_mode == "colour"


class TestPropagateMemberChange:
    def test_noop_for_ungrouped_device(self, engine, session, z2m_bulb):
        with patch("app.services.groups.engine", engine), \
             patch("app.services.groups.apply_device_command", new=AsyncMock()) as mock_apply:
            asyncio.run(groups_service.propagate_member_change(z2m_bulb.id))
        mock_apply.assert_not_awaited()

    def test_propagates_state_to_sibling(self, engine, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, z2m_bulb2.id]))

        z2m_bulb.state = True
        session.add(z2m_bulb)
        session.commit()

        with patch("app.services.groups.engine", engine), \
             patch("app.services.groups.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(groups_service.propagate_member_change(z2m_bulb.id))

        session.refresh(z2m_bulb2)
        assert z2m_bulb2.state is True
        mock_pub.assert_awaited_once_with(f"zigbee2mqtt/{z2m_bulb2.device_id}/set", {"state": "ON"})

    def test_skips_sibling_already_in_sync(self, engine, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, z2m_bulb2.id]))

        # bring both members to identical state first
        z2m_bulb2.state = z2m_bulb.state
        session.add(z2m_bulb2)
        session.commit()

        # already in sync — nothing should be sent
        with patch("app.services.groups.engine", engine), \
             patch("app.services.groups.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(groups_service.propagate_member_change(z2m_bulb.id))

        mock_pub.assert_not_awaited()

    def test_updates_group_mirrored_fields(self, engine, session, z2m_bulb, z2m_bulb2):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            group = asyncio.run(groups_service.create_group(session, "Lights", [z2m_bulb.id, z2m_bulb2.id]))

        z2m_bulb.brightness = 77
        session.add(z2m_bulb)
        session.commit()

        with patch("app.services.groups.engine", engine), \
             patch("app.services.groups.mqtt_client.publish", new=AsyncMock()):
            asyncio.run(groups_service.propagate_member_change(z2m_bulb.id))

        session.refresh(group)
        assert group.brightness == 77
