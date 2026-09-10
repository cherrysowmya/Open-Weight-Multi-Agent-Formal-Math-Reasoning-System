"""Regression tests for resource warnings exposed by V3 live integration."""
import io
import subprocess
import unittest
from unittest.mock import Mock

from local_lean_agent.config import LeanExploreConfig, LeanLSPConfig
from local_lean_agent.feedback.lean_lsp_mcp import LeanLSPMCPClient
from local_lean_agent.retrieval.lean_explore import LeanExploreMCPClient


class V3ServiceCleanupTests(unittest.TestCase):
    def clients(self):
        return (LeanLSPMCPClient(LeanLSPConfig()),
                LeanExploreMCPClient(LeanExploreConfig()))

    def test_close_releases_all_pipes_and_is_idempotent(self):
        for client in self.clients():
            for exited in (True, False):
                with self.subTest(client=type(client).__name__, exited=exited):
                    streams = [io.StringIO() for _ in range(3)]
                    process = Mock(stdin=streams[0], stdout=streams[1], stderr=streams[2])
                    process.poll.return_value = 0 if exited else None
                    selector = Mock()
                    client._process, client._selector = process, selector
                    client.close()
                    client.close()
                    self.assertTrue(all(stream.closed for stream in streams))
                    selector.close.assert_called_once()
                    self.assertIsNone(client._process)

    def test_forced_shutdown_still_closes_output_pipe(self):
        for client in self.clients():
            with self.subTest(client=type(client).__name__):
                output = io.StringIO()
                process = Mock(stdin=io.StringIO(), stdout=output, stderr=None)
                process.poll.return_value = None
                process.wait.side_effect = [subprocess.TimeoutExpired("test", 5),
                                            subprocess.TimeoutExpired("test", 5), 0]
                client._process = process
                client.close()
                process.terminate.assert_called_once()
                process.kill.assert_called_once()
                self.assertTrue(output.closed)


if __name__ == "__main__":
    unittest.main()
