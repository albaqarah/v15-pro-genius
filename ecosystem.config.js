module.exports = {
  apps: [{
    name: "bot-v15.4",
    script: "bot_v15.4_genius.py",
    interpreter: "/home/ubuntu/v15-pro-genius/.venv/bin/python3",
    cwd: "/home/ubuntu/v15-pro-genius",
    autorestart: true,
    max_restarts: 10,
    restart_delay: 5000,
    env: {
      PYTHONUNBUFFERED: "1"
    }
  }]
};
