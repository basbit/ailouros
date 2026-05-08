from __future__ import annotations


class MediaError(Exception):
    pass


class MediaProviderUnavailable(MediaError):
    pass


class MediaPolicyViolation(MediaError):
    pass
