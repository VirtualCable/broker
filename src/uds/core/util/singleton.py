import typing

_T = typing.TypeVar("_T")


class Singleton(type):
    """
    Metaclass for singleton pattern
    Usage:

    class MyClass(metaclass=Singleton):
        ...
    """

    _instance: typing.Any | None

    # Ensure "_instance" is not inherited
    def __init__(cls: "type[_T]", *args: typing.Any, **kwargs: typing.Any) -> None:
        """
        Initialize the Singleton metaclass for each class that uses it
        """
        # ``cls`` is typed as ``type[_T]`` so that ``__call__`` can return the
        # concrete class type instead of ``Any``; ``_instance`` lives on the
        # metaclass, so cast the class to ``Any`` to access it without a
        # ``type: ignore``.
        typing.cast(typing.Any, cls)._instance = None
        super().__init__(*args, **kwargs)

    def __call__(cls: "type[_T]", *args: typing.Any, **kwargs: typing.Any) -> _T:
        meta = typing.cast(typing.Any, cls)
        if meta._instance is None:
            meta._instance = super().__call__(*args, **kwargs)
        return typing.cast(_T, meta._instance)
