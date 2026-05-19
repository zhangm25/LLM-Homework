# AGENTS.md

## Project Goal

This project is a demo for a task-oriented intelligent travel planning assistant.

The system should understand vague natural language travel requests, infer missing destinations, search candidate POIs, generate multi-waypoint travel plans, rank plans, and explain the recommendation.

## Tech Stack

- Frontend: React + Vite
- Backend: FastAPI
- LLM API: Open-source LLM API
- Map API: AMap Web API or OpenRouteService
- Version Control: Git + GitHub

## Core Flow

User query
-> intent parsing
-> task planning
-> POI search
-> route planning
-> plan ranking
-> explanation generation
-> frontend display

## Development Rules

1. Do not hard-code API keys.
2. Use `.env` for secrets.
3. Keep backend and frontend separated.
4. LLM outputs must be validated as JSON.
5. If real map API is unavailable, use mock data first.
6. Do not modify unrelated files.
7. Prefer small, clear modules.
8. Keep all demo examples reproducible.
