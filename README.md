# Xauro — X (Twitter) AutoReply Bot

Desktop application for automated replying to X posts using AI. Features multi-account support, Telegram management, and anti-detection measures.

## Features

- **Multi-account X support** — manage multiple accounts with individual settings
- **AI-powered replies** — OpenAI, Gemini, Perplexity, Groq providers
- **Telegram management** — full bot control via Telegram with HITL approval
- **Anti-detection** — realistic delays, browser headers, proxy rotation
- **Desktop GUI** — Tkinter-based panel with live console
- **Flexible search** — keywords, X lists, or recommendations mode

## Project Structure

```
ai.py              # AI provider integrations
config.py         # Settings, encryption, delays, logging
db.py             # SQLite storage with aiosqlite
gui.py            # Tkinter desktop UI
main.py           # Entry point, worker loop, CLI
proxy.py          # Proxy rotation manager
state.py          # Shared runtime state (break circular imports)
tg_bot.py         # Telegram bot handlers
twitter.py        # X API client (search, post, like)
twitter_auth.py   # X authentication with anti-detect
```

## Requirements

- Python 3.11+
- OS: Windows, Linux, macOS
- Telegram bot token (from @BotFather)
- At least one AI provider API key

## Installation

```bash
# Clone and setup
git clone https://github.com/your-repo/xauro.git
cd xauro

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Generate encryption key
python main.py genkey

# Add account via CLI
python main.py add_account

# Start GUI
python main.py
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| ENCRYPTION_KEY | Fernet key for cookie encryption | auto-generated |
| OPENAI_API_KEY | OpenAI API key | - |
| GEMINI_API_KEY | Google Gemini API key | - |
| PERPLEXITY_API_KEY | Perplexity API key | - |
| GROQ_API_KEY | Groq API key | - |
| TELEGRAM_BOT_TOKEN | Telegram bot token | - |
| TELEGRAM_ADMIN_IDS | Admin user IDs | - |
| DEFAULT_AI_PROVIDER | Default AI provider | groq |

## Usage

### GUI Mode
```bash
python main.py
```

### Telegram Commands
- `/start` — show main menu
- `/menu` — refresh menu
- `/status` — show account status

### CLI Commands
```bash
python main.py genkey        # Generate encryption key
python main.py add_account  # Add X account
python main.py list_accounts # List accounts
python main.py test_session <id> # Test session
python main.py add_proxy <url>   # Add proxy
```

## How It Works

1. **Monitoring** — Bot searches for posts matching keywords/lists
2. **Selection** — Picks best tweet based on likes and comment availability
3. **AI Generation** — Generates reply using selected AI provider
4. **Approval** — Sends to Telegram for manual approval (or auto-posts)
5. **Posting** — Posts reply and likes original tweet
6. **Stats** — Logs activity and updates daily counters

## License

MIT License
