from myagentsdk import AgentBase, AgentConfig


class MyAgentOutbound(AgentBase):
    def __init__(self, config: AgentConfig, instructions: str) -> None:
        super().__init__(config, instructions)

    async def on_enter(self) -> None:
        await self.session.say(
            "Hello! I'm MyAgent, your AI assistant. How can I help you today?"
        )


def create_agent(config: AgentConfig, instructions: str) -> MyAgentOutbound:
    return MyAgentOutbound(config, instructions)
