from abc import ABC, abstractmethod


class BaseService(ABC):
    """Base class for all backend services. Supports model swapping."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def execute(self, input_data: dict) -> dict: ...
