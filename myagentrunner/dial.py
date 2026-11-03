import argparse
import asyncio
import os
import re
import uuid
from pathlib import Path

from .runner import resolve_agent_dir
from myagentsdk import ConfigError, load_config, load_environment


def _phone_number(value: str) -> str:
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise argparse.ArgumentTypeError(
            "phone number must be in E.164 format, for example +919876543210"
        )
    return value


async def place_outbound_call(agent_name: str, phone_number: str) -> str:
    from livekit import api

    agent_dir = resolve_agent_dir(agent_name, "outbound")
    config = load_config(agent_dir / "config.json")
    trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
    if not trunk_id or not trunk_id.startswith("ST_"):
        raise ConfigError("Set SIP_OUTBOUND_TRUNK_ID to your LiveKit outbound SIP trunk ID")

    room_name = f"outbound-{uuid.uuid4().hex}"
    participant_identity = f"phone-{uuid.uuid4().hex[:12]}"
    async with api.LiveKitAPI() as livekit_api:
        await livekit_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=config.name,
                room=room_name,
            )
        )
        try:
            await livekit_api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=room_name,
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=participant_identity,
                    wait_until_answered=True,
                ),
                timeout=45,
            )
        except (api.SipCallError, asyncio.TimeoutError):
            await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            raise

    return room_name


async def _run(args: argparse.Namespace) -> None:
    room_name = await place_outbound_call(args.agent_name, args.phone)
    print(f"Call answered. Agent room: {room_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Place one explicit outbound SIP call with a customer agent."
    )
    parser.add_argument("agent_name", help="Agent folder under myagentrunner/customer")
    parser.add_argument("--phone", required=True, type=_phone_number, help="E.164 phone number")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    load_environment(repo_root / ".env")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
