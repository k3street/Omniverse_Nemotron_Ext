# 10 — Conversational UX + Escalation Flows

## Purpose

Provide the conversational interface that ties all modules together. Allow users to ask questions, request changes, approve actions, understand reasoning, and receive escalation packages when the assistant cannot fix an issue.

## Runtime

Extension (UI), Background service (LLM orchestration)

## Phase

Continuous (Weeks 3–14, iterates alongside all phases)

## Dependencies

- Extension shell (01) — chat pane UI
- All other modules — the chat orchestrator routes user requests to the appropriate module

---

## Functional Requirements

### FR-10.1 Conversation Model

Support a multi-turn conversation with the following message types:

| Role | Type | Description |
|------|------|-------------|
| **User** | `text` | Free-form text question or instruction |
| **User** | `attachment` | Log file, screenshot, or error paste |
| **Assistant** | `text` | Explanatory text response |
| **Assistant** | `action_card` | Inline card for a proposed action (approve/reject/inspect) |
| **Assistant** | `source_card` | Inline card showing retrieved sources |
| **Assistant** | `analysis_card` | Inline card showing analysis results / findings |
| **Assistant** | `diff_card` | Inline card showing a proposed diff |
| **Assistant** | `escalation_card` | Escalation package with repro bundle link |
| **System** | `event` | Stage changed, snapshot created, fingerprint updated, etc. |

### FR-10.2 Intent Classification

Classify user messages into intents to route to the correct module:

| Intent | Route To | Example |
|--------|----------|---------|
| `diagnose` | Stage analyzer | "Why is my robot falling through the floor?" |
| `explain` | Retrieval + LLM | "What does PhysicsArticulationRootAPI do?" |
| `fix` | Patch planner | "Fix the missing collision on the gripper" |
| `inspect` | Stage analyzer | "What schemas are on /World/Robot?" |
| `rollback` | Snapshot manager | "Undo the last change" |
| `search_docs` | Source registry | "Show me the Isaac Lab reward function docs" |
| `search_knowledge` | Knowledge base | "Have we seen this error before?" |
| `code_assist` | Patch planner (code mode) | "Write a task config for pick-and-place" |
| `status` | Fingerprint + telemetry | "What version of Isaac Sim am I running?" |
| `settings` | Governance | "Switch to explain-only mode" |
| `escalate` | Escalation | "I give up, package this for the team" |
| `general` | LLM (direct) | "What's the difference between URDF and USD?" |

### FR-10.3 Context Management

Maintain a conversation context that includes:
- Conversation history (last N messages, configurable, default 50)
- Current selection context (prim paths, types, schemas)
- Current fingerprint summary
- Active analysis results (findings)
- Active patch plans (pending approval)
- Recent snapshots

When sending context to the LLM, include only relevant context for the current query (to manage token usage). Use a context window budget (configurable, default 8000 tokens for context, leaving room for response).

### FR-10.4 LLM Orchestration

The chat orchestrator calls the LLM with a structured prompt:

1. **System prompt:** Role definition, capabilities, constraints, output format instructions.
2. **Context block:** Fingerprint summary, selection context, active findings.
3. **Retrieved sources:** Top-K retrieval results relevant to the query.
4. **Knowledge matches:** Prior fixes/failures for similar issues.
5. **Conversation history:** Last N relevant messages.
6. **User message:** Current query.

The LLM response is parsed into structured output (text + action proposals + source references).

### FR-10.5 Response Rendering

Render assistant responses as rich cards in the chat pane:

- **Text responses:** Markdown-like rendering (headings, code blocks, lists, bold/italic).
- **Action cards:** Show proposed action, confidence badge, approve/reject buttons, "View diff" link.
- **Source cards:** Expandable cards showing source name, trust tier, URL, and chunk excerpt.
- **Analysis cards:** Collapsible findings list with severity icons and prim path links.
- **Diff cards:** Syntax-highlighted unified diff with accept/reject per hunk.

### FR-10.6 Prim Path Links

Any prim path mentioned in a response should be clickable:
- Clicking a prim path selects it in the viewport and stage tree.
- Use `omni.usd.get_context().get_selection().set_selected_prim_paths()`.

### FR-10.7 Code Assistance

For code-related requests:
- Write or modify Python scripts in the context of the loaded scene/workspace.
- Generate Isaac Lab task definitions, reward functions, observation configs.
- Generate extension config snippets.
- Always show generated code in a diff card (for new files: full file with syntax highlighting).
- Apply code to workspace only with user approval.

### FR-10.8 Escalation Flow

When the assistant determines it cannot fix an issue:
1. Inform the user clearly with the reason (low confidence, unknown issue class, compatibility blocker).
2. Offer to create a **repro bundle** containing:
   - Environment fingerprint
   - Stage snapshot (or minimal repro subset)
   - Conversation history (redacted)
   - Relevant log excerpts
   - List of attempted fixes and their outcomes
   - Suggested next steps and recommended sources/forums
3. Package the bundle as a zip file in the workspace.
4. Provide ranked external resources: NVIDIA forums, GitHub issues, relevant documentation sections.

### FR-10.9 Conversation Persistence

- Save conversation history to disk on session end (or periodically).
- Load previous conversation on extension startup (configurable: "Resume last session" toggle).
- Allow exporting conversation as Markdown.
- Allow clearing conversation history.

---

## Data Models

### ChatMessage

```python
@dataclass
class ChatMessage:
    message_id: str
    role: str                        # "user" | "assistant" | "system"
    message_type: str                # "text" | "action_card" | "source_card" | "analysis_card" | "diff_card" | "escalation_card" | "event" | "attachment"
    content: str                     # Text content or structured JSON
    timestamp: datetime
    
    # For assistant messages
    sources_cited: List[str]         # chunk_ids
    actions_proposed: List[str]      # action_ids
    intent_classified: Optional[str]
    confidence: Optional[float]
    
    # For user messages
    attachment_paths: List[str]

@dataclass
class ConversationContext:
    session_id: str
    messages: List[ChatMessage]
    
    # Live context
    selection_context: Optional[SelectionContext]
    fingerprint_summary: Dict[str, str]
    active_findings: List[str]       # finding_ids
    active_plans: List[str]          # plan_ids
    recent_snapshots: List[str]      # snapshot_ids (last 5)

@dataclass
class EscalationBundle:
    bundle_id: str
    created_at: datetime
    
    fingerprint: EnvironmentFingerprint
    snapshot_id: str
    conversation_excerpt: List[ChatMessage]  # Redacted
    attempted_fixes: List[str]               # plan_ids
    fix_outcomes: List[str]                  # validation results summary
    log_excerpts: List[str]
    recommended_sources: List[RetrievalResult]
    suggested_next_steps: List[str]
    
    bundle_path: str                         # Path to zip file
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/chat/message
  Request: {
    "session_id": str,
    "message": str,
    "attachments": [str],            # file paths
    "context": {
      "selection": SelectionContext | null,
      "fingerprint_summary": dict,
      "active_findings": [str],
      "active_plans": [str]
    }
  }
  Response: {
    "intent": str,
    "response_messages": [ChatMessage],
    "actions_to_approve": [PatchAction] | null,
    "sources_consulted": [RetrievalResult]
  }

POST /api/v1/chat/escalate
  Request: {
    "session_id": str,
    "reason": str,
    "include_conversation": bool,
    "include_snapshot": bool
  }
  Response: EscalationBundle

GET /api/v1/chat/sessions
  Query params: limit, offset
  Response: { "sessions": [{ "session_id": str, "started_at": datetime, "message_count": int }] }

GET /api/v1/chat/sessions/{session_id}
  Response: { "messages": [ChatMessage] }

DELETE /api/v1/chat/sessions/{session_id}
  Response: { "deleted": bool }

POST /api/v1/chat/sessions/{session_id}/export
  Request: { "format": "markdown" | "json" }
  Response: { "content": str } | { "file_path": str }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── chat/
        ├── __init__.py
        ├── orchestrator.py          # Intent classification + module routing
        ├── context_builder.py       # Build LLM context from live state
        ├── llm_provider.py          # LLM provider interface (abstract)
        ├── llm_claude.py            # Claude API implementation
        ├── response_parser.py       # Parse LLM output into structured messages
        ├── escalation.py            # Repro bundle packaging
        ├── session_store.py         # Conversation persistence
        └── routes.py

exts/
└── omni.isaac.assist/
    └── omni/isaac/assist/
        └── ui/
            ├── chat_view.py         # (from 01, detailed rendering here)
            ├── message_renderer.py  # Rich card rendering
            ├── code_view.py         # Syntax-highlighted code display
            └── prim_link.py         # Clickable prim path handler
```

---

## Implementation Notes

- **Intent classification:** For MVP, use keyword matching + a simple LLM classifier call. If the intent is ambiguous, default to `general` and let the LLM decide.
- **LLM provider abstraction:** Define a `LLMProvider` interface with `async def complete(messages, context) -> LLMResponse`. Ship with a Claude implementation. Allow swapping to local models or other providers.
- **Context window management:** Track token count for each context component. Prioritize: system prompt > current query > selection context > active findings > retrieved sources > conversation history (trim oldest first).
- **Secret redaction before LLM:** All context sent to the LLM must pass through the governance secret redactor (module 07).
- **Response streaming:** If the LLM provider supports streaming, stream text responses token-by-token into the chat view. Buffer action cards until complete.
- **Prim path detection:** Use regex to detect USD prim paths (strings starting with `/` matching `^/[A-Za-z0-9_/]+$`) in response text and make them clickable.
- **Escalation bundle size:** Limit snapshot inclusion to modified layers only. Redact secrets from conversation. Compress with gzip. Target: <50MB for typical bundles.
- **Conversation export as Markdown:** Include timestamps, role labels, and inline source citations. Omit binary attachments (reference by filename).

---

## Acceptance Criteria

- [ ] User can type a question and receive a contextual response.
- [ ] Intent classification correctly routes at least 8 of the 12 listed intents.
- [ ] Responses include inline source citations when retrieval is used.
- [ ] Action cards show approve/reject buttons and trigger the approval engine.
- [ ] Prim paths in responses are clickable and select the prim.
- [ ] Escalation produces a zip bundle with fingerprint, snapshot, redacted conversation, and suggested next steps.
- [ ] Conversation persists across extension restarts (when enabled).
- [ ] Conversation export produces valid Markdown.
- [ ] Code assistance generates syntactically valid Python for Isaac Lab tasks.
- [ ] Context sent to LLM respects token budget and excludes secrets.
