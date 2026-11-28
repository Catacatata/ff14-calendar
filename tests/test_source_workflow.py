"""执行实际工作流中的 Python 文件白名单防线；不操作任何 Git 仓库。"""
from pathlib import Path
import re
import textwrap
import unittest
from unittest.mock import patch

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/collect_sources.yml"


def guard_script(marker):
    text = WORKFLOW.read_text(encoding="utf-8")
    scripts = re.findall(r"python - <<'PY'\n(.*?)\n          PY", text, re.S)
    return next(textwrap.dedent(script) for script in scripts if marker in script)


class WorkflowGuardTests(unittest.TestCase):
    def test_index_allows_only_two_named_caches(self):
        script = guard_script("Unexpected staged paths")
        output = b"data/sources/bilibili.json\0data/sources/official_news.json\0"
        with patch("subprocess.check_output", return_value=output) as command:
            exec(compile(script, "workflow-index-guard", "exec"), {})
        self.assertIn("--cached", command.call_args.args[0])
        self.assertIn("--no-renames", command.call_args.args[0])

    def test_index_rejects_third_file_even_inside_sources_directory(self):
        script = guard_script("Unexpected staged paths")
        with patch("subprocess.check_output", return_value=b"data/sources/bilibili.json\0data/sources/private.txt\0"):
            with self.assertRaises(SystemExit):
                exec(compile(script, "workflow-index-guard", "exec"), {})

    def test_untracked_guard_rejects_non_whitelist_file(self):
        script = guard_script("Unexpected untracked workspace files")
        with patch("subprocess.check_output", return_value=b"data/sources/extra.json\0"):
            with self.assertRaises(SystemExit):
                exec(compile(script, "workflow-untracked-guard", "exec"), {})

    def test_workspace_guard_does_not_exclude_entire_sources_directory(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(":!data/sources/**", text)
        self.assertIn("':!data/sources/bilibili.json' ':!data/sources/official_news.json'", text)


if __name__ == "__main__":
    unittest.main()
