# UI Orientation

Where to find everything in the Isaac Assist interface.

---

## The Chat Panel

Open it via **Window > Isaac Assist** in the menu bar. It docks to the right side of the Isaac Sim window, below the Property panel.

The panel has four sections from top to bottom:

### Status Bar
Shows connection status to the backend service. Green = connected, red = service unreachable.

### Chat Area
The main conversation area. Messages appear as bubbles:

- **Your messages** — right-aligned
- **Isaac Assist responses** — left-aligned, may include:
    - Text explanations
    - **Code patch cards** — expandable blocks showing the generated Python/USD code
    - Data results (scene summaries, measurements, sensor specs)

### Input Area
A text field at the bottom where you type your requests. Press **Enter** or click **Send** to submit.

### Action Bar
Buttons below the input field:

| Button | What It Does |
|--------|--------------|
| **Settings** | Open the settings panel (LLM model, mode, API keys) |
| **Export Data** | Export conversation + tool calls for fine-tuning |
| **LiveKit Vision** | Toggle viewport streaming (if LiveKit is running) |
| **LiveKit Voice** | Toggle voice interaction (if LiveKit is running) |

---

## The Approval Dialog

When Isaac Assist generates code that modifies your scene, you'll see a **code patch card** in the chat:

1. The card shows the Python code that will run
2. A risk level indicator (low / medium / high)
3. Two buttons:
    - **Approve & Execute** — runs the code in Isaac Sim
    - **Reject** — discards the patch

!!! warning "Always review before approving"
    Isaac Assist shows you exactly what code will run. Take a moment to read it, especially for high-risk operations like deleting prims or running arbitrary scripts.

Every approved action is **undoable** — press `Ctrl+Z` in Isaac Sim to reverse it.

---

## Selection-Aware Context

Isaac Assist knows what you've selected in the viewport or stage tree.

1. **Click a prim** in the viewport or stage tree
2. The chat input shows the selected prim path (e.g., `[/World/MyCube]`)
3. Now when you type a request, Isaac Assist automatically includes context about that prim

This means you can say things like:

> Make this rigid body

instead of:

> Add rigid body physics to /World/MyCube

---

## Settings Panel

Click **Settings** in the action bar to configure:

| Setting | Options |
|---------|---------|
| **LLM Mode** | `local` (Ollama), `anthropic`, `cloud` (Gemini), `openai`, `grok` |
| **Model Name** | Depends on mode — e.g., `claude-sonnet-4-6` for Anthropic |
| **Auto-Approve** | Skip approval for low-risk operations |
| **Contribute Data** | Opt-in to share interaction data for model training |

!!! tip "Hot-switching LLM mode"
    You can switch between local and cloud models without restarting the service. Changes take effect on the next chat message.

---

## Where Things Are in Isaac Sim

For reference, the key Isaac Sim panels you'll interact with alongside Isaac Assist:

| Panel | Location | What It Shows |
|-------|----------|---------------|
| **Viewport** | Center | 3D view of your scene |
| **Stage** | Left sidebar | Tree view of all prims (objects) in the scene |
| **Property** | Right sidebar (above chat) | Properties of the selected prim |
| **Console** | Bottom | Log output, errors, warnings |
| **Timeline** | Bottom bar | Play/pause/stop simulation controls |
