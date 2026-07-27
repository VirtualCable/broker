import dataclasses


@dataclasses.dataclass(frozen=True)
class UDSClientPlugin:
    url: str
    description: str
    name: str
    legacy: bool

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "url": self.url,
            "description": self.description,
            "name": self.name,
            "legacy": self.legacy,
        }
