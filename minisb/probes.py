import hashlib
import mmap
import multiprocessing as mp
import os
import random
import socket
import time
from pathlib import Path

import numpy as np

MIB = 1024 * 1024
GIB = 1024 * MIB
PAGE = 4096


def timed_loop(window, step):
    count = 0
    start = time.perf_counter()
    while True:
        step()
        count += 1
        elapsed = time.perf_counter() - start
        if elapsed >= window:
            return count, elapsed


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("connection closed early")
        data += chunk
    return bytes(data)


class Probe:
    name = ""
    subsystem = ""
    unit = ""

    def setup(self, workdir):
        pass

    def check(self):
        return True

    def measure(self, window):
        raise NotImplementedError

    def teardown(self):
        pass


class CpuSha256(Probe):
    name = "cpu_sha256"
    subsystem = "cpu"
    unit = "MiB/s"
    block = b"x" * 65536

    def check(self):
        expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        return hashlib.sha256(b"abc").hexdigest() == expected

    def measure(self, window):
        count, elapsed = timed_loop(window, lambda: hashlib.sha256(self.block).digest())
        return count * len(self.block) / MIB / elapsed


class CpuMatmul(Probe):
    name = "cpu_matmul"
    subsystem = "cpu"
    unit = "GFLOP/s"
    size = 256

    def setup(self, workdir):
        rng = np.random.default_rng(1)
        self.a = rng.random((self.size, self.size))
        self.b = rng.random((self.size, self.size))
        self.c = np.empty_like(self.a)

    def check(self):
        a = np.array([[1.0, 2.0], [3.0, 4.0]])
        b = np.array([[5.0, 6.0], [7.0, 8.0]])
        return np.array_equal(a @ b, np.array([[19.0, 22.0], [43.0, 50.0]]))

    def measure(self, window):
        count, elapsed = timed_loop(window, lambda: np.matmul(self.a, self.b, out=self.c))
        return count * 2 * self.size**3 / 1e9 / elapsed


class MemCopy(Probe):
    name = "mem_copy"
    subsystem = "memory"
    unit = "GiB/s"
    size = 256 * MIB

    def setup(self, workdir):
        self.src = np.arange(self.size // 8, dtype=np.int64)
        self.dst = np.zeros_like(self.src)

    def check(self):
        np.copyto(self.dst, self.src)
        return np.array_equal(self.dst, self.src)

    def measure(self, window):
        count, elapsed = timed_loop(window, lambda: np.copyto(self.dst, self.src))
        return count * 2 * self.size / GIB / elapsed


class MemRandom(Probe):
    name = "mem_random"
    subsystem = "memory"
    unit = "Maccess/s"
    table_size = 256 * MIB
    accesses = 1 << 20

    def setup(self, workdir):
        rng = np.random.default_rng(2)
        self.table = np.arange(self.table_size // 8, dtype=np.int64)
        self.index = rng.integers(0, len(self.table), self.accesses, dtype=np.int64)
        self.out = np.empty_like(self.index)

    def check(self):
        np.take(self.table, self.index, out=self.out)
        return np.array_equal(self.out, self.index)

    def measure(self, window):
        count, elapsed = timed_loop(window, lambda: np.take(self.table, self.index, out=self.out))
        return count * self.accesses / 1e6 / elapsed


class DiskFsync(Probe):
    name = "disk_fsync"
    subsystem = "disk"
    unit = "op/s"
    slots = 256

    def setup(self, workdir):
        self.path = Path(workdir) / "probe-fsync.dat"
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        self.block = random.Random(3).randbytes(PAGE)
        self.slot = 0

    def write(self):
        os.pwrite(self.fd, self.block, (self.slot % self.slots) * PAGE)
        os.fdatasync(self.fd)
        self.slot += 1

    def check(self):
        os.pwrite(self.fd, self.block, 0)
        os.fdatasync(self.fd)
        return os.pread(self.fd, PAGE, 0) == self.block

    def measure(self, window):
        count, elapsed = timed_loop(window, self.write)
        return count / elapsed

    def teardown(self):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


class DiskRandRead(Probe):
    name = "disk_randread"
    subsystem = "disk"
    unit = "IOPS"
    file_size = 256 * MIB

    def setup(self, workdir):
        self.path = Path(workdir) / "probe-randread.dat"
        rng = random.Random(4)
        self.first_block = rng.randbytes(PAGE)
        with open(self.path, "wb") as output:
            output.write(self.first_block)
            chunk = rng.randbytes(MIB)
            for _ in range(self.file_size // MIB):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECT)
        self.buffer = mmap.mmap(-1, PAGE)
        self.blocks = self.file_size // PAGE
        self.rng = random.Random(5)

    def read(self):
        os.preadv(self.fd, [self.buffer], self.rng.randrange(self.blocks) * PAGE)

    def check(self):
        os.preadv(self.fd, [self.buffer], 0)
        return bytes(self.buffer) == self.first_block

    def measure(self, window):
        count, elapsed = timed_loop(window, self.read)
        return count / elapsed

    def teardown(self):
        os.close(self.fd)
        self.buffer.close()
        self.path.unlink(missing_ok=True)


def serve(listener):
    buffer = bytearray(MIB)
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            mode = recv_exact(conn, 1)
            if mode == b"e":
                while data := conn.recv(PAGE):
                    conn.sendall(data)
            elif mode == b"s":
                total = 0
                while size := conn.recv_into(buffer):
                    total += size
                conn.sendall(total.to_bytes(8, "little"))
            elif mode == b"h":
                digest = hashlib.sha256()
                while size := conn.recv_into(buffer):
                    digest.update(memoryview(buffer)[:size])
                conn.sendall(digest.digest())


class NetProbe(Probe):
    subsystem = "network"

    def setup(self, workdir):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.server = mp.get_context("fork").Process(target=serve, args=(self.listener,), daemon=True)
        self.server.start()
        self.address = self.listener.getsockname()

    def connect(self, mode):
        sock = socket.create_connection(self.address)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(mode)
        return sock

    def teardown(self):
        self.server.kill()
        self.server.join()
        self.listener.close()


class NetThroughput(NetProbe):
    name = "net_throughput"
    unit = "MiB/s"
    chunk = bytes(256 * 1024)

    def check(self):
        payload = random.Random(6).randbytes(MIB)
        with self.connect(b"h") as sock:
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            return recv_exact(sock, 32) == hashlib.sha256(payload).digest()

    def measure(self, window):
        sent = 0
        start = time.perf_counter()
        with self.connect(b"s") as sock:
            while time.perf_counter() - start < window:
                sock.sendall(self.chunk)
                sent += len(self.chunk)
            sock.shutdown(socket.SHUT_WR)
            received = int.from_bytes(recv_exact(sock, 8), "little")
        elapsed = time.perf_counter() - start
        if received != sent:
            raise RuntimeError(f"sent {sent} bytes, server received {received}")
        return sent / MIB / elapsed


class NetRtt(NetProbe):
    name = "net_rtt"
    unit = "roundtrip/s"
    message = bytes(64)

    def check(self):
        payload = b"ping" * 16
        with self.connect(b"e") as sock:
            sock.sendall(payload)
            return recv_exact(sock, len(payload)) == payload

    def measure(self, window):
        with self.connect(b"e") as sock:

            def roundtrip():
                sock.sendall(self.message)
                recv_exact(sock, len(self.message))

            count, elapsed = timed_loop(window, roundtrip)
        return count / elapsed


PROBES = {
    probe.name: probe
    for probe in (CpuSha256, CpuMatmul, MemCopy, MemRandom, DiskFsync, DiskRandRead, NetThroughput, NetRtt)
}
