# MARSHAL — Backend

One repo, two independent services.

marshal-backend/
├── express-api/        Node + Express + MongoDB/Mongoose + Clerk
│                        Owns: users, schedules, CRUD, auth, approvals
└── ml-agent-service/    Python + FastAPI + scikit-learn + LangGraph
                         Owns: duration/delay predictions, the agent

Express calls ml-agent-service over plain HTTP when it needs a
prediction or the agent's reasoning. Neither service touches the
other's database or files directly — HTTP is the only contract.

## First-time setup

express-api/
    cd express-api
    npm install
    cp .env.example .env

ml-agent-service/
    cd ml-agent-service
    python3 -m venv venv
    source venv/bin/activate      (Windows: venv\Scripts\activate)
    pip install -r requirements.txt
    cp .env.example .env

## Running both during development

Two terminals, one per service:
    Terminal 1: cd express-api && npm run dev
    Terminal 2: cd ml-agent-service && source venv/bin/activate && uvicorn main:app --reload

## MongoDB

Local dev uses Compass connected to mongodb://localhost:27017/marshal.
Going live later only changes MONGO_URI in express-api/.env — no code changes.

## Branching

- main is always the working state — never commit to it directly.
- Branch per task: <name>/<short-task>, e.g. sathwik/mongo-schema
- Pull main before starting new work each session.
