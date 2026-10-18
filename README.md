# myagentsdk and myagentrunner

`myagentsdk` provides reusable LiveKit `Agent` and `AgentSession` wrappers. `myagentrunner` discovers customer agents, loads their configuration and prompt, and delegates session startup to the agent's `_runtime.py`.

The example currently uses LiveKit Inference model IDs for STT, LLM, and TTS. The runner accepts both inbound and outbound agent folders; outbound SIP calling itself is not implemented yet.

## Layout

```text
myagentsdk/
  __init__.py
  agent.py           # shared configured LiveKit Agent base
  config.py          # validates config.json
  session.py         # creates and starts AgentSession from config
myagentrunner/
  runner.py          # resolves and starts a customer agent
  customer/
    MyAgent/
      inbound/
        agent.py
        config.json
        prompt.md
        _runtime.py
  pyproject.toml
```

Create each agent in `myagentrunner/customer/<AgentName>/<inbound|outbound>/` with `config.json`, `prompt.md`, `agent.py`, and `_runtime.py`. The runner loads the selected folder dynamically. Each `agent.py` defines `create_agent(config, instructions)` and subclasses `myagentsdk.AgentBase`; each `_runtime.py` defines `async start(ctx, create_agent, config)`. The runtime loads `prompt.md`, creates the configured agent, then calls the SDK session builder. Add agent-specific packages to that agent's requirements file when needed.

## Configure

The model strings in `config.json` are LiveKit Inference model IDs. Fill in `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in the repository `.env` file (start from `.env.example`) or set them in the process environment. The runner loads `.env` from the repository root. LiveKit Inference uses the LiveKit project credentials for model requests; direct provider credentials such as `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, and `CARTESIA_API_KEY` are included for agents that use provider plugins. Do not put secrets in `config.json`.

```json
{
  "name": "MyAgent",
  "models": {
    "stt": "deepgram/nova-3",
    "llm": "openai/gpt-4.1-mini",
    "tts": "cartesia/sonic-3"
  },
  "session": {
    "user_away_timeout": 15,
    "turn_detection": "adaptive",
    "interruption_mode": "adaptive",
    "endpointing": {
      "mode": "dynamic",
      "min_delay": 0.5,
      "max_delay": 3.0,
      "alpha": 0.9
    }
  }
}
```

`turn_detection` accepts `adaptive` (LiveKit automatic selection), `stt`, `vad`, `realtime_llm`, or `manual`. `endpointing.mode` independently accepts `fixed` or `dynamic`; `min_delay`, `max_delay`, and `alpha` configure its timing behavior. `interruption_mode` accepts `adaptive` or `vad`. The default values are `adaptive`, `adaptive`, and `fixed` respectively.

## Install and run

From the `myvoiveagent-sdk` directory, install the SDK and its LiveKit Agents dependency into your Python environment:

```powershell
python -m pip install -e .
```

Run an agent by its folder name and direction. The LiveKit worker's `dev`, `start`, or `console` mode is forwarded to the LiveKit CLI:

```powershell
myagentrunner MyAgent inbound dev
```

Or:

```powershell
python -m myagentrunner MyAgent inbound dev
```
