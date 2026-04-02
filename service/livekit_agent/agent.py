import logging
from dotenv import load_dotenv

load_dotenv("../isaac_assist_service/.env")

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.plugins import google, noise_cancellation
import os

logger = logging.getLogger(__name__)

class IsaacVoiceAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "You are Isaac Assist, a real-time Voice and Vision AI for NVIDIA Omniverse. "
            "The user is sharing their Isaac Sim viewport with you via the video stream. "
            "You should analyze their screen and help them debug physical simulations, Python scripts, "
            "and USD scene compositions. "
            "Keep your answers conversational, concise, and do not use complicated markdown formatting over voice."
        ))

server = AgentServer()

@server.rtc_session(agent_name="isaac-assist-agent")
async def my_agent(ctx: agents.JobContext):
    # Depending on LiveKit SDK version, realtime models are supported via google plugins
    # e.g., google.realtime.RealtimeModel() using Gemeni 1.5 Pro/Flash
    api_key = os.environ.get("API_KEY_GEMINI")
    if not api_key:
        logger.error("API_KEY_GEMINI must be set in .env")
        return
        
    session = AgentSession(
        llm=google.realtime.RealtimeModel(
            api_key=api_key,
            model="models/gemini-1.5-pro-latest"
        )
    )
    
    await session.start(
        room=ctx.room,
        agent=IsaacVoiceAssistant(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: noise_cancellation.BVC()
            )
        )
    )
    
    await session.generate_reply(
        instructions="Greet the user. Mention that you can now see their Isaac Sim viewport and ask how you can help."
    )

if __name__ == "__main__":
    agents.cli.run_app(server)
