"""Bounded retries for transient source failures."""

from collections.abc import Callable
from time import sleep
from typing import TypeVar

from tenacity import (
    Retrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
)
from tenacity import wait_random_exponential

from finintel.exceptions import TransientSourceError

T = TypeVar("T")


def run_with_retry(
    operation: Callable[[], T],
    before_attempt: Callable[[int], None],
    *,
    wait: Callable[[float], None] = sleep,
) -> T:
    def record_attempt(state: RetryCallState) -> None:
        before_attempt(state.attempt_number)

    retrying = Retrying(
        retry=retry_if_exception_type(TransientSourceError),
        wait=wait_random_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(3),
        before=record_attempt,
        sleep=wait,
        reraise=True,
    )
    return retrying(operation)
