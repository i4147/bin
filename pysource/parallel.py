from __future__ import annotations

import abc
import concurrent.futures as futures
import importlib.util
import multiprocessing as mp
import os
from typing import Callable, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_CHUNKSIZE = 1


def start_method() -> str:
    return "fork" if hasattr(os, "fork") else "spawn"


class ParallelRunner(abc.ABC):
    mode: str

    def __init__(self, workers: int = 0, chunksize: int = DEFAULT_CHUNKSIZE) -> None:
        self.workers = workers or os.cpu_count() or 1
        self.chunksize = max(1, chunksize)

    @abc.abstractmethod
    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        raise NotImplementedError

    def starmap(self, function: Callable[..., R], items: Iterable[tuple]) -> list[R]:
        payload = list(items)
        return self.map(lambda args: function(*args), payload)

    def run(self, function: Callable[..., R], items: Iterable[tuple]) -> list[R]:
        return self.starmap(function, items)

    def __enter__(self) -> "ParallelRunner":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        return None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(workers={self.workers})"


class SerialRunner(ParallelRunner):
    mode = "serial"

    def __init__(self, workers: int = 1, chunksize: int = DEFAULT_CHUNKSIZE, **_kwargs: object) -> None:
        super().__init__(1, chunksize)

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return [function(item) for item in items]


def _call(function: Callable[..., R], args: tuple) -> R:
    return function(*args)


def _unpack(payload: tuple[Callable[..., R], tuple]) -> R:
    return _call(*payload)


class MultiprocessingRunner(ParallelRunner):
    mode = "multiprocessing"

    def __init__(self, workers: int = 0, chunksize: int = DEFAULT_CHUNKSIZE, **kwargs: object) -> None:
        super().__init__(workers, chunksize)
        self._kwargs = kwargs
        self._pool: mp.pool.Pool | None = None

    def _new_pool(self) -> "mp.pool.Pool":
        context = mp.get_context(start_method())
        return context.Pool(processes=self.workers, **self._kwargs)

    def _ensure_pool(self) -> "mp.pool.Pool":
        if self._pool is None:
            self._pool = self._new_pool()
        return self._pool

    def starmap(self, function: Callable[..., R], items: Iterable[tuple]) -> list[R]:
        payload = [(function, tuple(args)) for args in items]
        if self.workers <= 1:
            return [_unpack(item) for item in payload]
        return self._ensure_pool().starmap(_call, payload, chunksize=self.chunksize)

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return self.starmap(function, [(item,) for item in items])

    def imap_unordered(self, function: Callable[..., R], items: Iterable[tuple]) -> Iterator[R]:
        payload = [(function, tuple(args)) for args in items]
        if self.workers <= 1:
            yield from (_unpack(item) for item in payload)
            return
        yield from self._ensure_pool().imap_unordered(_unpack, payload, chunksize=self.chunksize)

    def apply_async(self, function: Callable[..., R], *args: object) -> "mp.pool.AsyncResult[R]":
        return self._ensure_pool().apply_async(function, args)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool.join()
            self._pool = None

    def __exit__(self, *exc: object) -> None:
        self.close()


class FuturesRunner(ParallelRunner):
    mode = "futures"

    def __init__(self, workers: int = 0, chunksize: int = DEFAULT_CHUNKSIZE, submit_mode: bool = False) -> None:
        super().__init__(workers, chunksize)
        self.submit_mode = submit_mode
        self._executor: futures.Executor | None = None

    def _ensure_executor(self) -> "futures.Executor":
        if self._executor is None:
            self._executor = futures.ProcessPoolExecutor(max_workers=self.workers)
        return self._executor

    def submit_many(self, function: Callable[[T], R], items: Sequence[T]) -> list["futures.Future[R]"]:
        executor = self._ensure_executor()
        return [executor.submit(function, item) for item in items]

    def starmap(self, function: Callable[..., R], items: Iterable[tuple]) -> list[R]:
        payload = [(function, tuple(args)) for args in items]
        if self.workers <= 1:
            return [_unpack(item) for item in payload]
        if self.submit_mode:
            return [future.result() for future in self.submit_many(_unpack, payload)]
        return list(self._ensure_executor().map(_unpack, payload, chunksize=self.chunksize))

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return self.starmap(function, [(item,) for item in items])

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def __exit__(self, *exc: object) -> None:
        self.close()


class JoblibRunner(ParallelRunner):
    mode = "joblib"

    def __init__(self, workers: int = 0, chunksize: int = DEFAULT_CHUNKSIZE, backend: str | None = None) -> None:
        super().__init__(workers, chunksize)
        self.backend = backend

    def starmap(self, function: Callable[..., R], items: Iterable[tuple]) -> list[R]:
        from joblib import Parallel, delayed

        payload = list(items)
        if self.workers <= 1:
            return [function(*args) for args in payload]
        return Parallel(n_jobs=self.workers, backend=self.backend, batch_size=self.chunksize)(
            delayed(function)(*args) for args in payload
        )

    def map(self, function: Callable[[T], R], items: Sequence[T]) -> list[R]:
        return self.starmap(function, [(item,) for item in items])


def joblib_available() -> bool:
    try:
        importlib.util.find_spec("joblib")
    except (ImportError, ValueError):
        return False
    return True


MODES_BY_NAME: dict[str, type[ParallelRunner]] = {
    "": MultiprocessingRunner,
    "mp": MultiprocessingRunner,
    "multiprocessing": MultiprocessingRunner,
    "starmap": MultiprocessingRunner,
    "imap_unordered": MultiprocessingRunner,
    "apply_async": MultiprocessingRunner,
    "map": MultiprocessingRunner,
    "cf": FuturesRunner,
    "futures": FuturesRunner,
    "thread": FuturesRunner,
    "cf_map": FuturesRunner,
    "cf_submit": FuturesRunner,
    "joblib": JoblibRunner,
    "serial": SerialRunner,
    "none": SerialRunner,
}


def make_runner(mode: str = "", workers: int = 0, chunksize: int = DEFAULT_CHUNKSIZE, **kwargs: object) -> ParallelRunner:
    key = (mode or "").strip().lower()
    factory = MODES_BY_NAME.get(key)
    if factory is None:
        choices = ", ".join(sorted(set(MODES_BY_NAME) - {""}))
        raise ValueError(f"unknown parallel mode {mode!r}; choose one of: {choices}")
    if factory is JoblibRunner and not joblib_available():
        raise ValueError("parallel mode 'joblib' needs the joblib package")
    if factory is FuturesRunner:
        kwargs.setdefault("submit_mode", key == "cf_submit")
    if factory is SerialRunner:
        return SerialRunner(1)
    return factory(workers, chunksize, **kwargs)
