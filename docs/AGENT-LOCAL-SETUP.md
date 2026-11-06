# Local PartnerType agent

The agent runs only through a local Ollama service. The default configuration uses the
`partnerhub-agent` model, built from `qwen3:4b-instruct-2507` (see `Modelfile`). The code has no
cloud AI integration and never falls back to a cloud service.

For a producer instance, the AI model is installed by `python3 -m tools.instance ... create` (or
later by `... model`); see [instances](operations/INSTANCES.md). The manual steps below describe an
Ollama service on your own machine for development. The app needs an explicit environment and
secrets: a bare `python3 server.py` without configuration does not start.

## Requirements

- a computer with at least 16 GB RAM (8 GB can work, with little headroom),
- about 6–10 GB of free disk space for the model and Ollama data,
- Ollama running locally,
- internet only for the first model download.

## Installation

With the environment configured (see [DEPLOYMENT.md](DEPLOYMENT.md)), one command starts the
assistant and the app:

```sh
./scripts/start-local-demo.sh
```

The launcher starts the local Ollama service, installs the model the first time if needed, and
then starts PartnerType on port 8000. If the port is already taken, the script shows the process
that uses it instead of failing with an unreadable `Address already in use`.

Manual installation step by step:

1. Install Ollama from <https://ollama.com/download> and start the app.
2. In the project directory run:

   ```sh
   ./scripts/setup-local-agent.sh
   ```

3. Start PartnerType (with the environment configured):

   ```sh
   python3 server.py
   ```

4. Sign in as an administrator or a partner. The local AI assistant is available on both roles'
   dashboards. A green dot means the model is ready.

You can also check the service with:

```sh
curl -s http://127.0.0.1:11434/api/tags
```

This is the check to rely on — it asks the service, not the binary.

**macOS note:** after installing from `Ollama.app`, the `ollama` command **is not on the `PATH`**.
`ollama list` then returns `command not found`, even though the service works. The binary is inside
the app bundle:

```sh
/Applications/Ollama.app/Contents/Resources/ollama list
```

`scripts/setup-local-agent.sh` and `scripts/start-local-demo.sh` have this fallback built in
(`OLLAMA_BIN`), so the commands they run work without configuring `PATH`.

## Optional configuration

The defaults are safe for a demo. You can change them without touching the code:

```sh
OLLAMA_URL=http://127.0.0.1:11434 \
OLLAMA_MODEL=partnerhub-agent \
python3 server.py
```

## What happens to the data

- Conversation text and tool results go only from the PartnerType backend to Ollama at the local
  address `127.0.0.1`.
- The integration sends no prompts to Google, OpenAI or any other cloud provider.
- Conversation history is kept only in the process memory for at most 30 minutes and never goes to
  the audit log.
- An operation proposal token is valid for 10 minutes and bound to the signed-in user. The record
  is deleted after use, cancellation, an attempt to use it after expiry, or during clean-up when the
  next proposal is prepared.
- CRM notes are treated as untrusted data, not as instructions for the model.

Local processing limits data transfer to third parties, but on its own it does not make you
compliant with GDPR, NIS2 or the AI Act. In production you still need access control, retention,
backups, a record of processing activities, risk assessment and incident response procedures,
among others.

## Operation rules

- A partner can read only their own leads and prepare a new lead submission.
- An administrator can read all leads and prepare a stage change.
- The model does not accept leads, does not calculate commissions and does not create leads on its
  own.
- Every change shows a summary card and needs a separate click.
- Confirmation re-checks the current stage, queue and commission calculation; an outdated proposal
  is refused.

## Common problems

"Port 8000 is already in use" means another PartnerType instance or another app is running. Stop
the process the launcher shows (preferably `Ctrl+C` in the terminal where it was started), then run
`./scripts/start-local-demo.sh` again. Do not run two instances on the same database.

"Ollama is not running on this computer" means the local service does not answer. Start the Ollama
app or `ollama serve`.

"The partnerhub-agent model is missing" means Ollama runs, but the model was not installed or the
installation failed. Run the setup script again.
