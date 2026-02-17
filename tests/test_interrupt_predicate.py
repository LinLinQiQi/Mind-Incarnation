from __future__ import annotations

import unittest

from mi.providers.codex_runner import _should_interrupt_command


class TestInterruptPredicate(unittest.TestCase):
    def test_off(self) -> None:
        self.assertFalse(_should_interrupt_command("off", "/bin/zsh -lc 'pip install x'"))

    def test_on_any_external(self) -> None:
        self.assertTrue(_should_interrupt_command("on_any_external", "/bin/zsh -lc 'pip install x'"))
        self.assertTrue(_should_interrupt_command("on_any_external", "/bin/zsh -lc 'curl https://x'"))
        self.assertTrue(_should_interrupt_command("on_any_external", "/bin/zsh -lc 'npm publish'"))
        self.assertTrue(_should_interrupt_command("on_any_external", "/bin/zsh -lc 'sudo rm -rf /tmp/x'"))

    def test_on_high_risk(self) -> None:
        self.assertFalse(_should_interrupt_command("on_high_risk", "/bin/zsh -lc 'pip install x'"))
        self.assertTrue(_should_interrupt_command("on_high_risk", "/bin/zsh -lc 'git push origin main'"))
        self.assertTrue(_should_interrupt_command("on_high_risk", "/bin/zsh -lc 'npm publish'"))
        self.assertTrue(_should_interrupt_command("on_high_risk", "/bin/zsh -lc 'sudo rm -rf /tmp/x'"))


if __name__ == "__main__":
    unittest.main()
