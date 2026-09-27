"""Opt-in, content-free request failure observations for the gateway."""

from contextlib import contextmanager
from contextvars import ContextVar

import requests


_observer = ContextVar('obsidian_request_failure_observer', default=None)


def failure_details(error):
    """Inspect exception types and numeric API codes, never exception text."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, requests.HTTPError):
            details = {'category': 'api_http'}
            response = error.response
            if response is not None:
                status = response.status_code
                if type(status) is int and 100 <= status <= 599:
                    details['http_status'] = status
                    if status in (401, 403):
                        details['category'] = 'api_auth'
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                code = payload.get('errorCode') if isinstance(payload, dict) else None
                if type(code) is int and 10000 <= code <= 59999:
                    details['api_error_code'] = code
            return details
        for error_type, category in (
            (requests.exceptions.SSLError, 'api_tls'),
            (requests.Timeout, 'api_timeout'),
            (requests.ConnectionError, 'api_connection'),
            (requests.RequestException, 'api_request'),
        ):
            if isinstance(error, error_type):
                return {'category': category}
        error = error.__cause__ or error.__context__
    return {'category': 'tool_error'}


@contextmanager
def observe_request_failures(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def report_request_failure(error):
    callback = _observer.get()
    if callback is not None:
        # Diagnostics must not change a write's outcome or trigger retries.
        try:
            callback(failure_details(error))
        except Exception:
            pass
