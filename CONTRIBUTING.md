# Contributing to JobFarm

We welcome contributions to JobFarm! Whether it is adding new portal adapters, improving anti-detection evasion, refining AI question answering, or enhancing cloud IaC, here is how you can help.

## Development Setup
1. Fork and clone the repository:
   ```bash
   git clone https://github.com/jobfarm/jobfarm.git
   cd jobfarm
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Run tests:
   ```bash
   pytest automation_monorepo/tests/
   ```

## Code Guidelines
- Follow PEP 8 style guidelines.
- Keep portal adapters resilient against DOM shifts and dynamic class names.
- Ensure all AI prompts maintain compatibility across local (Ollama) and cloud (DeepSeek/OpenAI/Gemini) backends.
- Add tests for new adapters, screening logic, or parser rules.

## Pull Request Process
1. Create a feature branch: `git checkout -b feature/portal-lever-enrichment`
2. Commit changes with descriptive commit messages.
3. Verify all tests pass.
4. Open a Pull Request referencing any related issues.
