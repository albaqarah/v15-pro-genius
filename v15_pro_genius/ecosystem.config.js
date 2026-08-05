// PM2 config — pm2 start ecosystem.config.js && pm2 logs v15-genius
module.exports = {
  apps: [
    {
      name: "v15-genius",
      script: "bot_pro_genius_v15.py",
      interpreter: "./venv/bin/python3",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 50,
      restart_delay: 5000,
      max_memory_restart: "600M",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        TZ: "Asia/Jakarta",
      },
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-err.log",
      time: true,
    },
  ],
};
