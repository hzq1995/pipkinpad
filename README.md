# PipkinPad

`PipkinPad` is a locally-run Python workbench: manage files in the current directory, use a persistent terminal, and let an OpenAI API-compatible AI assist with processing work tasks.

## Installation & Startup

```bash
pip install pipkinpad
cd your-project
pipkinpad start
```

The app listens on `127.0.0.1:8765` by default. Use `--port 9000` to change the port. By default the browser is not opened automatically; add `--browser` to open it on start. The startup directory is the workspace root — the service and AI cannot access paths outside it.

To use the same workbench from another device on a trusted network, start it on the host machine with:

```bash
pipkinpad start --host 0.0.0.0
```

Then visit `http://HOST_IP:8765` from the other device. Do not expose this service to an untrusted network or the public internet.

Configure the model (keys are never echoed):

```bash
pipkinpad config --base-url https://api.openai.com/v1 --model gpt-4.1-mini --api-key YOUR_KEY
pipkinpad config
pipkinpad clear-config
```

### Password protection

Set a password before starting the server to require login in the browser:

```bash
pipkinpad config --password YOUR_PASSWORD
```

After a successful login, PipkinPad remembers that browser for 30 days using an
HTTP-only signed cookie. Changing the password invalidates previous logins. To
return to password-free local access:

```bash
pipkinpad config --clear-password
```

The password is never stored directly. Its salted PBKDF2 hash and the cookie
signing secret are kept inside PipkinPad's encrypted local settings file.

You can also fill in the "Configure API" panel on the app's right sidebar. Connection parameters and API keys are encrypted and saved to a local config file; the master encryption key is stored in the operating system credential store.

## Security Model

- A random local session token is generated on every startup; the file API and terminal WebSocket require this token.
- File operations are path-normalized and restricted to the startup directory; hidden files are not shown in the tree.
- The AI only sees chat history and files or terminal output you explicitly attach.
- The AI can only propose commands. The page provides a confirmation button for each Bash command; once confirmed, the command runs in the workspace's isolated Bash process, and output is handed back to the AI for further processing. The AI never writes to the human-facing Terminal; when leveraging Terminal context, it can only read its output.
- UI state is saved in `.pipkinpad-ui-state.json` in the workspace, so devices connected to the same PipkinPad service share layout, tabs, context settings, and a Terminal-output snapshot.
- Audit events are saved to `.pipkinpad-audit.jsonl` in the workspace; API keys and terminal input are never logged.
