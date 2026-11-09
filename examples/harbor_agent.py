"""Sketch of a Harbor installed agent. This file has not been executed.

Harbor is not a dependency of this repo, and CI does not install it.
Nothing here is a Terminal-Bench score.

Docs: https://docs.harborframework.com/agents/custom-agents
Benchmark: https://github.com/harbor-framework/terminal-bench
Dataset: https://hub.harborframework.com/datasets/terminal-bench/terminal-bench

The class below follows the installed-agent shape in those docs: Harbor would
install this project inside the task container and run the CLI there. The
container is Harbor's sandbox. This repo's command policy would still apply
inside that container, which may refuse commands a task expects. That has
not been measured.

Run shape, from the Harbor docs (not run here):

    harbor run -d terminal-bench/terminal-bench \\
        -a examples.harbor_agent:TerminalAgent \\
        -m openai/your-model
"""

from __future__ import annotations

import shlex

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class TerminalAgent(BaseInstalledAgent):
    @staticmethod
    def name() -> str:
        return "terminal-agent"

    def version(self) -> str | None:
        return "0.1.0"

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_agent(
            environment,
            command="pip install terminal-agent",
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # --confirm allow still denies the hard-denied commands (rm -rf /,
        # sudo, pipe-to-shell). It does not disable the allowlist unless
        # --allow-any is also set. Pick the flags deliberately; this sketch
        # has not been tried on the benchmark.
        command = (
            "terminal-agent run "
            + shlex.quote(instruction)
            + " --provider openai --confirm allow --workdir /tmp/terminal-agent-work"
        )
        await self.exec_as_agent(environment, command=command)
