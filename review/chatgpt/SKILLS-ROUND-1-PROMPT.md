You're the second senior lifecycle operator and prompt engineer on Wavy OS, the operating system of Wavy Studios, an email and SMS retention agency for DTC brands. You built Wavy's client hubs, so you know how the agency works.

The attached file holds the agent **skill files**: 5 role skills and 23 area skills. AI agents load them to do real client work. The bundle also includes the format they must follow, the permission catalog and the agent tools. It contains no client data, and skills must never contain client data.

How agents use them: each task's work packet carries the role skill of the agent doing the work, plus the ten sections of the task's area skill. Each area's Quality checklist becomes the default checklist that a QA agent on a different model passes or fails, item by item, from the output alone.

Optimize these skills for agent execution and client value. I want:

1. **Errors to fix now.** Any wrong regulatory or platform rule, wrong number, unsafe instruction, a permission that's wrong for its risk, or a contradiction between skills. Cite file and section, explain why it's wrong, and give the exact replacement text.

2. **Per-file edits.** For each file, the highest-value changes only, as exact replacement text:
   - vague steps an agent can't execute
   - checklist items QA can't judge from the output alone
   - missing decision rules
   - examples that don't teach
   Use this format:
   ```
   FILE: areas/<name>.md
   SECTION: <section heading>
   REPLACE: <exact current text, or "ADD AFTER: <exact anchor line>">
   WITH: <new text>
   WHY: <one line>
   ```
   Keep each file inside its existing ten-section structure. Don't add sections.

3. **Gaps against the founder's spec.** Anything the 23 areas should cover but don't, and where it belongs.

4. **Scores.** One line per file, from 1 to 10, for executability and for client value.

Be direct and specific. No praise, no generic best practice. Skip anything that's already right.

End with `VERDICT: <ready | fix-then-ready | rework>` and the five most important fixes, in order.
