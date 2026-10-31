"""Run CPU-bound engine searches outside the API process."""
import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor


async def run_in_process(fn, *args):
    """Run picklable CPU-bound engine work in a fresh child process, terminated on cancellation.

    A search holds the GIL for seconds to minutes; in a thread it would starve the API's
    request handling and clock. A separate process keeps the API responsive and lets a
    cancelled or timed-out negotiation stop its search immediately.
    """
    executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
    finally:
        # shutdown() cannot stop a running task; terminate the worker so a cancelled search ends now.
        for proc in list((getattr(executor, "_processes", None) or {}).values()):
            if proc.is_alive():
                proc.terminate()
        executor.shutdown(wait=False, cancel_futures=True)
