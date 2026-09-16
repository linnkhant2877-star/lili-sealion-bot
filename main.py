import os
import staypresent

# Koyeb က ပေးတဲ့ port ကို ဖတ်ပြီး health check server ကို run မယ်
staypresent.web.json({"status": "running"})
staypresent.run("bot.py", port=int(os.getenv("PORT", 8080)))
