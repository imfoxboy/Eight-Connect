📦 Install Dependencies
>pip install -r requirements.txt

🚀 Run the Application
>uvicorn main:app --reload

---
🗂 Project Structure
```
.
├── main.py                 # Entry point
├── config.py               # Configuration
├── gateway/                # Core proxy logic and routing
├── client/                 # Outbound request logic
├── schemas/                # Pydantic data models
├── utils/                  # Utility modules: DB, logger, etc.
├── requirements.txt        # Dependencies
├── Dockerfile              # Container image definition
├── README.md               # This file
├── IQONO_INTEGRATION.md    # Full integration description
└── RP_IQONO_CONFIG.md      # RP / GC configuration examples
```
