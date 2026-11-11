import asyncio
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, TypeVar

from httpx import HTTPStatusError


ResultKeyT = TypeVar("ResultKeyT", bound=Hashable)

# Upper bound on the number of upstream requests a single batch may issue.
MAX_BATCH_TASKS = 250


@dataclass(kw_only=True)
class AsyncTask(Generic[ResultKeyT]):
    """A task for batch processing.

    - args and kwargs are intended to be passed into the func as func(*args, **kwargs)
    - result_key can be used to map to the result. result_key is usually an identifier
        like "SPGI".
    - The result and error attributes are used to store the result of the execution.
        They should not be modified directly outside of batch_execute_async_tasks.
    - AsyncTask can be expanded as needed, for example to also return the source urls for
        requests.
    """

    func: Callable[..., Awaitable[Any]]
    args: Any = field(default_factory=tuple)
    kwargs: Any = field(default_factory=dict)
    result_key: ResultKeyT
    result: Any = None
    error: str | None = None


async def batch_execute_async_tasks(tasks: list[AsyncTask[ResultKeyT]]) -> None:
    """Execute a list of tasks in the with up to 10 parallel tasks.

    The results from the execution (result or error) are directly stored in the task.
    Raises a ValueError if more than MAX_BATCH_TASKS tasks are passed in.
    """
    if len(tasks) > MAX_BATCH_TASKS:
        raise ValueError(
            f"Refusing to execute {len(tasks)} tasks in one batch (maximum is {MAX_BATCH_TASKS})."
        )

    # Allow a maximum of 10 tasks to run at once.
    throttle = asyncio.Semaphore(10)
    await asyncio.gather(*[execute_task_with_throttle(task, throttle) for task in tasks])


async def execute_task_with_throttle(
    task: AsyncTask[ResultKeyT], throttle: asyncio.Semaphore
) -> None:
    """Execute a single task and store the result in the task's `result` attribute."""

    async with throttle:
        try:
            result = await task.func(*task.args, **task.kwargs)
            task.result = result
        except HTTPStatusError as http_err:
            error_code = http_err.response.status_code
            if error_code == 404:
                task.error = f"No result found for {task.result_key}."
            elif error_code == 400:
                task.error = http_err.response.text
            # Non-400/404 errors are likely indicative of a substantial error and
            # should be raised.
            else:
                raise http_err
