# CLAUDE.md

## User preferences

- In manual mode, use the Read, Write, and Edit tools to change files rather than bash commands (e.g. no `sed`, `echo >`, `cat <<EOF`).
- Always provide a summary of what a bash command does (plain-language description) whenever one is run.
- Whenever the user explains instructions for the next steps in a project, ask questions to further detail their requirements for that step before starting work.
- When working on multi-step plans check in with me after each step and give me a brief summary of what has changed and what is next. 
- Use git to commit changes, push large changes.