# Quick Start

Get Isaac Assist running in under 10 minutes.

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/k3street/Omniverse_Nemotron_Ext.git
cd Omniverse_Nemotron_Ext
```

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 3: Configure the Environment

```bash
cp service/isaac_assist_service/.env.example service/isaac_assist_service/.env
```

Edit `.env` and choose your LLM mode:

=== "Local (Ollama)"

    ```env
    LLM_MODE=local
    LOCAL_MODEL_NAME=cosmos-reason-2:latest
    ```

    Then pull the model:
    ```bash
    ollama pull cosmos-reason-2:latest
    ```

=== "Claude (Anthropic)"

    ```env
    LLM_MODE=anthropic
    API_KEY_ANTHROPIC=sk-ant-...
    ```

=== "Gemini (Google)"

    ```env
    LLM_MODE=cloud
    CLOUD_MODEL_NAME=gemini-1.5-pro-latest
    API_KEY_GEMINI=AIza...
    ```

=== "OpenAI / GPT"

    ```env
    LLM_MODE=openai
    API_KEY_OPENAI=sk-...
    ```

## Step 4: Start the Service

```bash
uvicorn service.isaac_assist_service.main:app --host 0.0.0.0 --port 8000 --reload
```

!!! tip "Using the launch script"
    You can also use the interactive launcher:
    ```bash
    ./launch_service.sh           # Interactive menu
    ./launch_service.sh anthropic # Direct mode selection
    ```

Verify the service is running:

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"isaac-assist-backend"}
```

## Step 5: Launch Isaac Sim

```bash
./launch_isaac.sh
```

Wait for Isaac Sim to fully load (you see the viewport with a ground plane or empty stage).

!!! note "Custom Isaac Sim path"
    If your Isaac Sim is installed somewhere non-standard:
    ```bash
    export ISAAC_SIM_PATH=/path/to/your/isaac-sim
    ./launch_isaac.sh
    ```

## Step 6: Open the Chat Panel

In Isaac Sim's menu bar:

**Window > Isaac Assist**

The chat panel appears docked on the right side.

## Step 7: Verify Everything Works

Type in the chat panel:

> Hello, what can you do?

Isaac Assist should respond with a summary of its capabilities. If you see an error like `Failed to communicate with service`, double-check that the FastAPI service is running (Step 4).

---

## What's Next?

- [UI Orientation](ui-orientation.md) — Learn where everything is in the interface
- [Your First Task](first-task.md) — Create, physics-enable, and simulate an object
- [Tools Reference](../reference/tools.md) — Browse all available tools
