"""Errors that stay inside one data source."""


class SourceError(Exception):
    """A single indicator failed and must not abort the whole run.

    ``code`` is a stable token. Notes and the Telegram text map from it.
    The message stays generic so API keys never land in the CSV.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
