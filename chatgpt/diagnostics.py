"""Content-free JSON diagnostics on stderr; stdout belongs to MCP."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
import sys
from uuid import uuid4
from log_store import BoundedLog


class Diagnostics:
    def __init__(self):
        self.run_id = uuid4().hex
        directory = os.getenv('OBSIDIAN_DIAGNOSTICS_DIR')
        self.store = BoundedLog(directory) if directory else None

    def emit(self, event, **fields):
        record = {
            'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'run_id': self.run_id,
            'pid': os.getpid(),
            'event': event,
            **fields,
        }
        line = json.dumps(record, separators=(',', ':')) + '\n'
        if self.store is not None:
            try:
                self.store.write(line)
                return
            except (OSError, ValueError):
                pass
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass

    @contextmanager
    def capture_library_logs(self):
        """Use only in the dedicated gateway process, never the shared client."""
        root = logging.getLogger()
        previous_handlers, previous_level = root.handlers[:], root.level
        handler = RedactedLibraryHandler(self)
        root.handlers = [handler]
        root.setLevel(logging.WARNING)
        try:
            yield
        finally:
            root.handlers = previous_handlers
            root.setLevel(previous_level)
            handler.close()


class RedactedLibraryHandler(logging.Handler):
    def __init__(self, diagnostics):
        super().__init__(logging.WARNING)
        self.diagnostics = diagnostics

    def emit(self, record):
        # SDK warnings may contain tool arguments, names, paths or traceback
        # values. Do not format record.msg, record.args or record.exc_info.
        source = 'mcp' if record.name == 'mcp' or record.name.startswith('mcp.') else 'other'
        event = 'library_error' if record.levelno >= logging.ERROR else 'library_warning'
        self.diagnostics.emit(event, source=source)
