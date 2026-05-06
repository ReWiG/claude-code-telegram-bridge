"""Tests for cctg.pty_bridge."""
import os
import pytest
import time
from cctg.pty_bridge import PTYBridge


class TestPTYBridge:
    def test_create_pty_pair(self):
        bridge = PTYBridge(cwd="/tmp")
        master_fd, slave_fd = bridge._create_pty()
        assert master_fd > 0
        assert slave_fd > 0
        os.close(master_fd)
        os.close(slave_fd)

    def test_spawn_child(self):
        bridge = PTYBridge(cwd="/tmp")
        master_fd, slave_fd = bridge._create_pty()
        try:
            pid = bridge._spawn_child(slave_fd, ["/bin/echo", "hello"])
            assert pid > 0
            os.waitpid(pid, 0)
        finally:
            os.close(master_fd)
            # slave_fd is already closed by _spawn_child in the parent

    def test_start_and_is_alive(self):
        bridge = PTYBridge(cwd="/tmp")
        bridge.start(["/bin/sleep", "5"])
        assert bridge.is_alive()
        assert bridge.child_pid > 0
        bridge.stop()

    def test_read_output(self):
        bridge = PTYBridge(cwd="/tmp")
        bridge.start(["/bin/echo", "test_output"])
        time.sleep(0.3)
        output = bridge.read_output()
        assert "test_output" in output
        bridge.stop()

    def test_write_input(self):
        bridge = PTYBridge(cwd="/tmp")
        bridge.start(["/bin/cat"])
        time.sleep(0.2)
        bridge.write_input("hello_from_test")
        time.sleep(0.2)
        output = bridge.read_output()
        # cat echoes back what it receives
        assert "hello_from_test" in output
        bridge.stop()

    def test_stop_cleanup(self):
        bridge = PTYBridge(cwd="/tmp")
        bridge.start(["/bin/sleep", "30"])
        assert bridge.is_alive()
        bridge.stop()
        time.sleep(0.2)
        assert not bridge.is_alive()
