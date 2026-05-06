"""PTY Bridge — creates PTY, spawns process, multiplexes I/O."""
from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import termios


class PTYBridge:
    def __init__(self, cwd: str, extra_env: dict[str, str] | None = None):
        self.cwd = cwd
        self.extra_env = extra_env or {}
        self.master_fd: int | None = None
        self.child_pid: int | None = None
        self._running = False

    def _create_pty(self) -> tuple[int, int]:
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        return master_fd, slave_fd

    def _spawn_child(self, slave_fd: int, cmd: list[str]) -> int:
        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(self.master_fd)
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            # Configure slave: no echo, raw input (no line buffering), CR→NL
            try:
                attrs = termios.tcgetattr(0)
                attrs[0] = attrs[0] | termios.ICRNL   # map \r to \n on input
                attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)
                termios.tcsetattr(0, termios.TCSANOW, attrs)
            except OSError:
                pass
            # Set terminal properties
            os.environ["TERM"] = os.environ.get("TERM", "xterm-256color")
            for k, v in self.extra_env.items():
                os.environ[k] = v
            # Copy window size from parent terminal
            try:
                size = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\x00" * 8)
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, size)
            except OSError:
                pass
            os.chdir(self.cwd)
            os.execvp(cmd[0], cmd)
            os._exit(1)
        else:
            # Parent process
            os.close(slave_fd)
            # Set non-blocking
            fl = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            return pid

    def start(self, cmd: list[str], extra_env: dict[str, str] | None = None) -> None:
        if extra_env:
            self.extra_env = extra_env
        self.master_fd, slave_fd = self._create_pty()
        self.child_pid = self._spawn_child(slave_fd, cmd)
        self._running = True

    def stop(self) -> None:
        self._running = False
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGTERM)
                # Non-blocking wait to reap zombie
                try:
                    os.waitpid(self.child_pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            except ProcessLookupError:
                pass
            self.child_pid = None
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

    def is_alive(self) -> bool:
        if not self.child_pid:
            return False
        try:
            pid, status = os.waitpid(self.child_pid, os.WNOHANG)
            if pid == self.child_pid:
                # Child exited — reap it
                self.child_pid = None
                self._running = False
                return False
            return True
        except ChildProcessError:
            self.child_pid = None
            return False

    def write_input(self, text: str) -> None:
        """Write text + Enter to PTY master (appears as stdin to child)."""
        if self.master_fd is not None and self._running:
            try:
                os.write(self.master_fd, (text + "\r").encode())
            except (OSError, BrokenPipeError):
                pass

    def read_output(self) -> str:
        """Read available output from PTY master."""
        if self.master_fd is None or not self._running:
            return ""
        data = b""
        try:
            while True:
                r, _, _ = select.select([self.master_fd], [], [], 0)
                if not r:
                    break
                chunk = os.read(self.master_fd, 4096)
                if not chunk:
                    self._running = False
                    break
                data += chunk
        except (OSError, BlockingIOError):
            pass
        return data.decode("utf-8", errors="replace")
