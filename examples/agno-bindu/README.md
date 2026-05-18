# Exposing this MCP server as a network-addressable Bindu agent

This directory contains a complete, working example of how to take the
`mcp-taiwan-legal-db` Model Context Protocol (MCP) server and expose it
as a network-addressable autonomous agent over the Agent-to-Agent (A2A)
JSON-RPC protocol, using [Bindu](https://github.com/GetBindu/Bindu).

It is contributed by the team at **Bindu**, where we are building a
**compliance operating system for small and medium businesses**. The
agent in this directory ("Lex Taiwan") is one of the reference agents we
ship as part of that work, and the present pull request is the result of
integrating your MCP server into our compliance stack. We will link back
to this repository from our public documentation and example index so
that anyone exploring Bindu can find your project as the canonical way
to access Taiwan's legal corpora.

---

## What the example does

A program written against this example can ask Taiwan legal questions in
plain Chinese or English over a standard HTTP endpoint and receive
answers that are grounded entirely in the eight tools your MCP server
exposes — with citations to the underlying judgments, regulations, and
constitutional interpretations. The agent is given a structured system
prompt that requires it to call your tools rather than rely on the
language model's training memory, and to cite each statement against the
primary source it came from.

The agent uses two open-source libraries:

- **Bindu** — the framework we are building. It turns a Python function
  into a network-addressable agent with its own cryptographic identity
  (a Decentralized Identifier, or DID) and a public agent card,
  reachable over the Agent-to-Agent JSON-RPC protocol defined at
  https://github.com/google-a2a/A2A.
- **agno** — used as the internal agent loop. It handles the
  language-model API call, the tool-calling protocol, and short-term
  conversation memory.

The MCP server itself comes from this repository, unmodified. It is
launched as a child process and the agent communicates with it over
standard input and output, as defined by the Model Context Protocol.

---

## How the pieces connect

```
┌─────────────────────────┐   HTTP request    ┌─────────────────────────────┐
│ Any A2A-aware client    │ ────────────────▶ │  Bindu agent on :3773       │
│ (curl, another agent,   │                   │  (agent card + DID)         │
│  a frontend, …)         │                   └─────────────────────────────┘
└─────────────────────────┘                                 │
                                                            ▼
                                              ┌─────────────────────────────┐
                                              │  Language model             │
                                              │  (via OpenRouter)           │
                                              └─────────────────────────────┘
                                                            │
                                                  child process pipe (stdio)
                                                            ▼
                                              ┌─────────────────────────────┐
                                              │  mcp_server (this repo)     │
                                              │  eight read-only tools      │
                                              └─────────────────────────────┘
                                                            │
                                                            ▼
                                   司法院  ·  law.moj.gov.tw  ·  憲法法庭
```

---

## Setup

The example reuses your repository's own virtual environment. The MCP
server is launched directly from the local `mcp_server` package, so no
separate installation of `mcp-taiwan-legal-db` from PyPI is required.

From the root of this repository, run:

```bash
# 1. Complete the upstream "Development setup" first, if you haven't.
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 2. Install the additional packages this example needs.
.venv/bin/pip install -r examples/agno-bindu/requirements.txt

# 3. Provide a language-model API key.
cp examples/agno-bindu/.env.example examples/agno-bindu/.env
# Open the .env file and set OPENROUTER_API_KEY to your key
# from https://openrouter.ai/keys
```

The default model is `anthropic/claude-sonnet-4.5`, selected because it
performs well on both tool use and Traditional Chinese. You can switch
the model by setting `BINDU_AGENT_MODEL` in the `.env` file to any
identifier supported by OpenRouter — for example `openai/gpt-4o`,
`google/gemini-2.5-pro`, or `deepseek/deepseek-chat`.

---

## Running the agent

The primary entry point is the Bindu A2A service:

```bash
.venv/bin/python examples/agno-bindu/bindu_agent.py
```

This starts a network-addressable agent on port 3773. The MCP server is
started once when the program loads and is kept running across all
subsequent requests, which avoids the cost of restarting it for every
question. The endpoints the service exposes are documented in the API
reference below.

For quick local checks without spinning up the network service, you can
also use the command-line script. It opens the MCP server, asks one
question, prints the cited answer, and exits:

```bash
.venv/bin/python examples/agno-bindu/cli.py "民法第 184 條的現行條文是什麼？"
.venv/bin/python examples/agno-bindu/cli.py "釋字 748 引用了哪些更早的釋字？"
.venv/bin/python examples/agno-bindu/cli.py \
  "Find Supreme Court cases about 借名登記 from 民國 113 onwards. Return only the case identifier and a one-line holding."
```

---

## API reference

The Bindu A2A service exposes the endpoints below at
`http://localhost:3773` by default. The shapes shown here apply to any
A2A deployment of this example.

### `GET /.well-known/agent.json`

Returns the agent's public agent card. The card describes who the agent
is, what it can do, and how to talk to it. Any A2A-aware client should
fetch this first.

**Example response (abridged):**

```json
{
  "id": "44b10e18-be36-03c7-1941-6c393c32e5b8",
  "name": "lex-taiwan",
  "description": "An agentic Taiwan legal research assistant: judgments, regulations, and constitutional court interpretations, sourced live from the 司法院, 全國法規資料庫, and 憲法法庭.",
  "url": "http://localhost:3773",
  "version": "2026.20.8",
  "protocolVersion": "1.0.0",
  "kind": "agent",
  "capabilities": {
    "streaming": false,
    "pushNotifications": false,
    "extensions": [
      {
        "uri": "did:bindu:you_at_example_com:lex-taiwan:44b10e18-be36-03c7-1941-6c393c32e5b8",
        "description": "DID-based identity for lex-taiwan",
        "required": false
      }
    ]
  },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [],
  "agentTrust": {
    "identityProvider": "custom",
    "trustVerificationRequired": false
  }
}
```

The `extensions[].uri` field contains the agent's Decentralized
Identifier (DID), which uniquely identifies this agent instance.

### `GET /health`

Returns a small JSON health-check payload. Useful for liveness probes.

**Example response:**

```json
{
  "version": "2026.20.3",
  "health": "healthy",
  "runtime": {
    "storage_backend": "InMemoryStorage",
    "scheduler_backend": "InMemoryScheduler",
    "task_manager_running": true,
    "strict_ready": true
  },
  "application": {
    "penguin_id": "2d5b0908-4384-bb67-8c8c-3c2801999e96",
    "agent_did": "did:bindu:you_at_example_com:lex-taiwan:2d5b0908-4384-bb67-8c8c-3c2801999e96"
  },
  "system": {
    "python_version": "3.14.2",
    "platform": "Darwin",
    "environment": "development"
  },
  "status": "ok",
  "ready": true,
  "uptime_seconds": 12.3
}
```

The `application.agent_did` field is the same DID returned in the
agent card.

### `POST /` (JSON-RPC 2.0)

This is the main endpoint. It accepts JSON-RPC 2.0 requests. The two
methods this example supports are described below.

#### Method: `message/send`

Submits a new question to the agent. The agent runs asynchronously, so
this method does not return the final answer directly. It returns a
task object that includes a `taskId`. The client then polls
`tasks/get` until the task reaches a terminal state.

**Request body:**

```json
{
  "jsonrpc": "2.0",
  "id": "<a UUID for this RPC call>",
  "method": "message/send",
  "params": {
    "configuration": {
      "acceptedOutputModes": ["text/plain"]
    },
    "message": {
      "role": "user",
      "messageId": "<a UUID>",
      "contextId": "<a UUID identifying the conversation>",
      "taskId":    "<a UUID identifying the task>",
      "kind": "message",
      "parts": [
        { "kind": "text", "text": "釋字 748 解釋日期？一句話。" }
      ]
    }
  }
}
```

All four identifier fields (`id`, `messageId`, `contextId`, `taskId`)
must be valid UUIDs. The `contextId` should be reused across requests
that belong to the same conversation; a fresh `taskId` is used for each
new question.

**Response body (the task has been accepted but is not finished yet):**

```json
{
  "jsonrpc": "2.0",
  "id": "<echoes your request id>",
  "result": {
    "id": "<the taskId you sent, echoed back>",
    "contextId": "<the contextId you sent>",
    "status": { "state": "submitted" },
    "artifacts": [],
    "history": []
  }
}
```

#### Method: `tasks/get`

Fetches the current state of a previously submitted task. Clients
typically call this in a polling loop until the task reaches a terminal
state.

**Request body:**

```json
{
  "jsonrpc": "2.0",
  "id": "<a fresh UUID for this RPC call>",
  "method": "tasks/get",
  "params": {
    "taskId": "<the taskId returned from message/send>"
  }
}
```

**Response body when the task is finished:**

```json
{
  "jsonrpc": "2.0",
  "id": "<echoes your request id>",
  "result": {
    "id": "<the taskId>",
    "contextId": "<the contextId>",
    "status": { "state": "completed" },
    "artifacts": [
      {
        "artifactId": "<a UUID>",
        "parts": [
          {
            "kind": "text",
            "text": "司法院釋字第 748 號於民國 106 年 5 月 24 日作成。\n\n### Sources\n- 司法院釋字第 748 號【同性二人婚姻自由案】(106-05-24) https://cons.judicial.gov.tw/docdata.aspx?fid=100&id=310929&rn=8467"
          }
        ]
      }
    ]
  }
}
```

The `status.state` field can take any of the following values:

| State              | Meaning                                                       |
|--------------------|---------------------------------------------------------------|
| `submitted`        | The request was accepted; processing has not started yet.     |
| `working`          | The agent is processing the request.                          |
| `input-required`   | The agent needs clarification before it can continue.         |
| `completed`        | The agent has finished; the answer is in `artifacts`.         |
| `failed`           | The agent could not complete the request.                     |

#### Error responses

JSON-RPC errors follow the standard JSON-RPC 2.0 shape:

```json
{
  "jsonrpc": "2.0",
  "id": null,
  "error": {
    "code": -32700,
    "message": "Failed to parse JSON payload. Please ensure the request body contains valid JSON syntax.",
    "data": "<the underlying validation error, when applicable>"
  }
}
```

### A worked end-to-end example

The shell snippet below sends one question and prints the final answer.

```bash
# Generate the four UUIDs the A2A spec requires.
RPC_ID=$(uuidgen  | tr 'A-Z' 'a-z')
MSG_ID=$(uuidgen  | tr 'A-Z' 'a-z')
CTX_ID=$(uuidgen  | tr 'A-Z' 'a-z')
TASK_ID=$(uuidgen | tr 'A-Z' 'a-z')

# Submit the question.
curl -s -X POST http://localhost:3773 \
  -H 'content-type: application/json' \
  -d "{
    \"jsonrpc\":\"2.0\",\"id\":\"$RPC_ID\",\"method\":\"message/send\",
    \"params\":{
      \"configuration\":{\"acceptedOutputModes\":[\"text/plain\"]},
      \"message\":{
        \"role\":\"user\",
        \"messageId\":\"$MSG_ID\",
        \"contextId\":\"$CTX_ID\",
        \"taskId\":\"$TASK_ID\",
        \"kind\":\"message\",
        \"parts\":[{\"kind\":\"text\",\"text\":\"釋字 748 解釋日期？一句話即可。\"}]
      }
    }
  }"

# Poll until the task is finished.
while true; do
  RESP=$(curl -s -X POST http://localhost:3773 \
    -H 'content-type: application/json' \
    -d "{
      \"jsonrpc\":\"2.0\",
      \"id\":\"$(uuidgen | tr 'A-Z' 'a-z')\",
      \"method\":\"tasks/get\",
      \"params\":{\"taskId\":\"$TASK_ID\"}
    }")
  STATE=$(echo "$RESP" | jq -r '.result.status.state // "?"')
  echo "state: $STATE"
  case "$STATE" in
    completed|failed|input-required)
      echo "$RESP" | jq -r '.result.artifacts[0].parts[0].text'
      break
      ;;
  esac
  sleep 2
done
```

### The eight MCP tools

For completeness, the agent has access to the eight tools your server
exposes: `search_judgments`, `get_judgment`, `query_regulation`,
`get_pcode`, `search_regulations`, `get_interpretation`,
`search_interpretations`, and `get_citations`. The agent selects among
them automatically based on the question. The authoritative reference
for each tool is your top-level README's "What you get / Tool details"
section.

---

## How the agent is instructed

The system prompt (`prompts.py`) is the part of the example that turns a
general-purpose language model into a Taiwan legal research assistant.
It is broken into labelled sections so each rule the agent must follow is
unambiguous, and it pins the agent to four behaviours:

1. **Every answer must come from your MCP server.** The agent is
   instructed never to answer from training memory when the question
   touches a Taiwan statute, judgment, or constitutional interpretation.
2. **The agent must cite every claim.** Judgments are cited by their
   case identifier (`JID`), regulations by their name and article
   number, and constitutional interpretations by their number and
   issuance date.
3. **The agent prefers the cheapest precise call.** When a regulation
   name is known, it resolves the `pcode` first and then fetches the
   article, rather than running a keyword search. When a case identifier
   is known, it uses `search_judgments(case_word, case_number,
   year_from)` instead of stuffing the identifier into the `keyword`
   field.
4. **The agent declines questions outside Taiwan's jurisdiction** and
   asks for clarification when the question is genuinely ambiguous,
   instead of guessing.

---

## Verified end-to-end

The following queries were executed against the real MCP server, hitting
`law.moj.gov.tw`, `judgment.judicial.gov.tw`, and
`cons.judicial.gov.tw`, during integration testing of this example.

| #  | Method      | Question                                                   | Outcome                                                                          |
|----|-------------|------------------------------------------------------------|----------------------------------------------------------------------------------|
| 1  | CLI         | 民法第 184 條第 1 項全文                                    | Returned 民法 184 第 1 項 verbatim, with pcode `B0000001` and the source URL.     |
| 2  | CLI         | 釋字 748 的解釋日期和確認的權利                              | Returned 106-05-24, 婚姻自由 and 平等權.                                          |
| 3  | CLI         | 最高法院 cases about 借名登記 from 民國 113 onwards         | Returned three real cases with valid case identifiers.                            |
| 4  | CLI         | 釋字 748 引用了哪些更早的釋字？                              | Returned 釋字 242 / 362 / 365 / 554 / 585 / 647 with brief context per citation.  |
| 5  | A2A service | 民法 184 第 2 項原文                                        | Task completed; answer included pcode and source URL.                             |
| 6  | A2A service | 釋字 748 的解釋日期                                          | Task completed; answer was "中華民國 106 年 5 月 24 日".                           |
| 7  | A2A service | 最高法院 預售屋 遲延交屋 cases from 民國 113 onwards         | Task completed; returned `TPSV,113,台上,637,20241225,1` with a one-line holding.  |
| 8  | A2A service | "What's the most recent US Supreme Court ruling…?"          | Task completed; politely declined and pointed the user to SCOTUSblog / Justia.    |
| 9  | A2A service | "Tell me about the marriage case."                          | Task completed; asked which case (釋字 748 vs. 憲判字 8 vs. others) before acting.|

Tests 8 and 9 demonstrate that the prompt rules around scope and
ambiguity take effect without any tool call, which is the desired
behaviour.

---

## Files in this directory

```
examples/agno-bindu/
├── README.md           This document.
├── prompts.py          The system prompt and the agent's description.
├── agent.py            The agent definition (model, instructions, memory).
├── cli.py              The one-shot command-line runner.
├── bindu_agent.py      Exposes the agent over the A2A protocol via Bindu.
├── requirements.txt    Additional Python packages required by this example.
├── .env.example        Required environment variables.
└── .gitignore          Local artefacts that should not be committed.
```

---

## A note on scope and dependencies

This example does not modify any code under `mcp_server/`, does not
change your CI configuration, and does not add any new top-level
dependencies to your project. All extra packages live in
`examples/agno-bindu/requirements.txt` and are strictly opt-in.

The intent is to make it easy for downstream users to extend your MCP
server into a fully autonomous agent without forking your repository.
If at any point the maintainers would prefer a different structure (for
example, moving the example to its own repository), the Bindu team is
happy to accommodate.

---

## About the contributing team

Bindu (https://github.com/GetBindu/Bindu) is a framework for building
autonomous AI agents as microservices, each one identified by a
Decentralized Identifier, each one able to talk to other agents over
the Agent-to-Agent protocol. We are using Bindu to build a **compliance
operating system for small and medium businesses** — a system in which
jurisdiction-specific agents (tax, labour, data protection, contract
review, and so on) answer questions and draft documents on a company's
behalf, grounded in primary sources.

The Lex Taiwan agent in this directory is the first jurisdiction-specific
agent in our showcase, and this pull request is the result of
integrating your MCP server into our stack. We will be linking back to
your repository from the Bindu documentation, the Bindu examples
directory, and any public material in which Lex Taiwan appears, so that
anyone who discovers the agent through Bindu can also discover the work
you have published here.

Thank you for open-sourcing this server. If there is anything we can do
to make this integration more useful to your project — additional
documentation, alternative example structures, or upstream contributions
to `mcp_server/` itself — we would be glad to help.
