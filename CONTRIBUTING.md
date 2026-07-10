# Contributing to RAG Agent

## Development Setup

```bash
# Clone and enter project
git clone <repo-url>
cd RAG_Agent

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env       # edit with your API keys

# Frontend
cd ../frontend
npm install
```

## Run

```bash
# Development (both backend + frontend)
cd RAG_Agent
python main.py

# Or start separately
cd backend && uvicorn main:app --reload
cd frontend && npm run dev
```

## Commands

### Backend

| Command | Purpose |
|---------|---------|
| `pytest tests/ -v` | Run all tests |
| `pytest tests/ --cov=. --cov-report=term-missing` | Tests with coverage |
| `ruff check .` | Lint |
| `mypy . --config-file ../pyproject.toml` | Type check |

### Frontend

| Command | Purpose |
|---------|---------|
| `npm test` | Run Vitest unit tests |
| `npm run build` | TypeScript check + production build |
| `npm run lint` | Lint (oxlint) |
| `npm run test:e2e` | Playwright E2E (requires running backend) |

## Code Style

- **Python**: ruff for formatting, mypy for types. Configured in root `pyproject.toml`.
- **TypeScript**: oxlint for linting, strict TypeScript. Vite handles build.

## Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new feature
- `fix:` bug fix
- `refactor:` code change without feature/fix
- `test:` adding or updating tests
- `docs:` documentation
- `chore:` maintenance (CI, deps, etc.)

## Pull Request Checklist

- [ ] Backend: `pytest tests/`, `ruff check .`, `mypy .` all pass
- [ ] Frontend: `npm run build`, `npm test` pass
- [ ] New features include tests
- [ ] No secrets or `.env` files committed

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module diagrams and data flow.

## Configuration

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all environment variables.
