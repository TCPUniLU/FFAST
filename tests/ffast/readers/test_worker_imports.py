"""A reader worker process holds BLAS to one thread.

Readers import ase.io, which imports scipy, whose OpenBLAS starts one thread
per core in every process that loads it. A cluster job reads a big file with
256 worker processes, so 256 x 64 threads were asked for: thread creation
failed, workers died, and the load stopped with "A process in the process pool
was terminated abruptly" (MeluXina, shard_00.extxyz, 2026-11-13).
"""
import os

from ffast.io.extxyz_arrays import worker_pool


def _env(name):
    return os.environ.get(name)


def _blas_threads_after_loading_the_readers():
    import ffast.readers  # noqa: F401  (imports ase.io, and with it scipy)
    import threadpoolctl

    return sorted({info["num_threads"] for info in threadpoolctl.threadpool_info()
                   if info["user_api"] == "blas"})


def test_a_reader_worker_starts_with_one_blas_thread():
    with worker_pool(1) as pool:
        assert pool.submit(_env, "OPENBLAS_NUM_THREADS").result() == "1"
        assert pool.submit(_env, "OMP_NUM_THREADS").result() == "1"
        # [] where threadpoolctl cannot see the BLAS (numpy 2 on macOS uses
        # Accelerate); any BLAS it sees has one thread.
        assert set(pool.submit(_blas_threads_after_loading_the_readers).result()) <= {1}
