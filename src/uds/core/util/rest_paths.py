#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Pure REST path utilities, usable from any layer (no REST imports).
"""

import typing


def normalize_rest_path(path: str) -> str:
    """Normalize a REST path to its canonical relative form.

    Strips surrounding slashes, lowercases, and removes any leading service
    prefix (``uds/rest``, ``rest`` or ``uds``), so values like ``uds/rest/mcp``,
    ``/mcp/`` and ``mcp`` all normalize to ``mcp``.
    """
    result = path.strip().strip("/").lower()
    changed = True
    while changed and result:
        changed = False
        for prefix in ("uds/rest", "rest", "uds"):
            if result == prefix:
                result, changed = "", True
            elif result.startswith(prefix + "/"):
                result, changed = result[len(prefix) + 1 :], True
    return result


def rest_path_allowed(requested_path: str, allowed_paths: list[typing.Any]) -> bool:
    """Return True when ``requested_path`` falls inside any of ``allowed_paths``.

    Entries are REST-path scopes relative to the REST root. An entry matches
    when it equals the requested path or is a prefix of it at a segment
    boundary, so scope ``providers`` covers ``providers/{uuid}/services``.
    """
    requested = normalize_rest_path(requested_path)
    return any(
        (scope := normalize_rest_path(str(entry))) and (requested == scope or requested.startswith(scope + "/"))
        for entry in allowed_paths
    )
