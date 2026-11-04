"""Small HTTP transport with bounded transient retries and redacted failures."""

import time

import httpx

from photo_metadata_agent.inference.contracts import ProviderError


def post_json(url: str, body: dict, headers: dict, timeout: float, attempts: int) -> dict:
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.post(url, json=body, headers=headers)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    time.sleep(min(2**attempt, 4))
                    continue
            if not response.is_success:
                raise ProviderError(f"Provider returned HTTP {response.status_code}.")
            result = response.json()
            if not isinstance(result, dict):
                raise ProviderError("Provider returned an invalid response object.")
            return result
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt + 1 == attempts:
                raise ProviderError("Provider connection failed or timed out.") from exc
            time.sleep(min(2**attempt, 4))
        except ValueError as exc:
            raise ProviderError("Provider returned invalid JSON.") from exc
    raise ProviderError("Provider attempts exhausted.")
