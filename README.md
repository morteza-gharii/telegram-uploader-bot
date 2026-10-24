# Telegram Uploader Bot

A Telegram bot that receives files from users, stores only the file's `file_id`
(files stay on Telegram's own servers), and returns a shareable deep link:

```
https://t.me/<bot>?start=file_<id>
```

## Features

- Users send any file and get a dedicated download link
- Batch mode: several files under one link
- Per-user language choice (Persian / English)
- Files sent via deep links auto-delete after a configurable delay
- Optional anti-forward (`protect_content`) and per-language promo captions
- Download counters, file deletion, multi-admin support, file search
- Forced subscription to channels, manageable by admins from inside the bot
- Button-based admin panel, broadcast, stats, block / unblock users

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the example config and fill in your own values:

```bash
cp config.example.json config.json
```

`config.json`:

```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
  "owner_id": 0
}
```

- `token`: the token from BotFather.
- `owner_id`: your own numeric Telegram user ID.

4. Run the bot:

```bash
python bot.py
```

## Security notes

- **Never commit your real `config.json`.** It is already listed in
  `.gitignore`, together with the database, logs and virtual environments.
- This repository contains no bot token, no owner ID, no user data and no logs.
  The database (`bot.db`) is created locally on first run.
