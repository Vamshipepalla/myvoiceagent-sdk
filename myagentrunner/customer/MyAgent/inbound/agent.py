from myagentsdk import AgentBase, AgentConfig


class MyAgent(AgentBase):
    def __init__(self, config: AgentConfig, instructions: str) -> None:
        super().__init__(config, instructions)


def create_agent(config: AgentConfig, instructions: str) -> MyAgent:
    return MyAgent(config, instructions)
