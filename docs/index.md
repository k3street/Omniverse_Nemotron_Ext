# Isaac Assist

**Natural language control for NVIDIA Isaac Sim.**

Isaac Assist is a dockable AI chat panel inside Isaac Sim that lets you build, modify, debug, and control robotics simulations by typing plain English. Instead of navigating menus, editing properties, or writing Python scripts, you describe what you want and Isaac Assist generates the code, shows it to you for approval, and executes it.

---

## What Can You Do With Isaac Assist?

| Category | Examples |
|----------|---------|
| **Create objects** | "Create a cube at 0, 0, 0.5" / "Add a sphere named Ball" |
| **Add physics** | "Add rigid body physics to /World/Box" / "Make this cloth" |
| **Import robots** | "Import a Franka Panda robot" / "Load the Nova Carter" |
| **Control simulation** | "Play the simulation" / "Step 10 frames" / "Reset" |
| **Add sensors** | "Attach a RealSense D435i to the wrist link" |
| **Create materials** | "Make this box look like brushed steel" |
| **Debug issues** | "Why is the robot falling through the floor?" |
| **Build scenes** | "Set up a tabletop manipulation scene with a Franka" |
| **ROS2 integration** | "List all ROS2 topics" / "Publish a Twist to /cmd_vel" |
| **Motion planning** | "Move the end-effector to position 0.5, 0, 0.3" |

---

## How It Works

```
You type a request in the chat panel
        |
        v
Isaac Assist understands your intent
        |
        v
It picks the right tool(s) and generates code
        |
        v
You review and approve the code patch
        |
        v
Isaac Sim executes the change (Ctrl+Z to undo)
```

Every action goes through an **approval flow** — Isaac Assist never modifies your scene without your explicit consent. And every change is undoable via Ctrl+Z.

---

## Quick Links

- **New here?** Start with the [Quick Start](getting-started/quick-start.md)
- **Looking for a specific tool?** See the [Tools Reference](reference/tools.md)
- **Something not working?** Check [Troubleshooting](reference/troubleshooting.md)
- **Want to understand the architecture?** Read the [Architecture Overview](architecture/overview.md)

---

## System Requirements

| Requirement | Version |
|-------------|---------|
| NVIDIA Isaac Sim | 5.1 or 6.0 |
| Python | 3.10+ |
| GPU | NVIDIA RTX (required by Isaac Sim) |
| Docker | Latest (only for LiveKit voice features) |
| Ollama | Latest (only for local LLM mode) |
