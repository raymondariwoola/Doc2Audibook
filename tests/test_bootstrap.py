import unittest
from unittest import mock

import bootstrap


class BootstrapWebTests(unittest.TestCase):
    def test_web_frontend_is_skipped_for_minimal_or_no_web(self):
        with mock.patch("bootstrap.shutil.which") as which:
            bootstrap.build_web_frontend(minimal=True, no_web=False)
            bootstrap.build_web_frontend(minimal=False, no_web=True)
        which.assert_not_called()

    def test_web_frontend_uses_clean_install_and_build(self):
        def executable(name):
            return f"C:/tools/{name}"

        with (
            mock.patch("bootstrap.shutil.which", side_effect=executable),
            mock.patch(
                "bootstrap.subprocess.check_output",
                return_value="v22.13.0\n",
            ),
            mock.patch("bootstrap.run") as run,
        ):
            bootstrap.build_web_frontend(minimal=False, no_web=False)

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(["C:/tools/npm", "ci"], cwd=bootstrap.ROOT / "web"),
                mock.call(
                    ["C:/tools/npm", "run", "build"],
                    cwd=bootstrap.ROOT / "web",
                ),
            ],
        )

    def test_old_node_skips_frontend_with_clear_boundary(self):
        def executable(name):
            return f"C:/tools/{name}"

        with (
            mock.patch("bootstrap.shutil.which", side_effect=executable),
            mock.patch(
                "bootstrap.subprocess.check_output",
                return_value="v20.18.0\n",
            ),
            mock.patch("bootstrap.run") as run,
        ):
            bootstrap.build_web_frontend(minimal=False, no_web=False)

        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
