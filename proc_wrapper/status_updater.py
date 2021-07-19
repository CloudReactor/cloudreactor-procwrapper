#!/usr/local/bin/python

# Copyright (c) 2021-present Machine Intelligence Services, Inc.  All rights reserved.
#
# This software is provided "as is," without warranty of any kind,
# express or implied. In no event shall the author or contributors
# be held liable for any damages arising in any way from the use of
# this software.
#
# This software is dual-licensed under open source and commercial licenses:
#
# 1. The software can be licensed under the terms of the Mozilla Public
# License Version 2.0:
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# 2. The commercial license gives you the full rights to create
# and distribute software on your own terms without any open source license
# obligations.

import atexit
import json
import logging
import os
import socket
from typing import Any, Dict, Optional


def _exit_handler(updater):
    atexit.unregister(_exit_handler)
    updater.shutdown()


class StatusUpdater:
    DEFAULT_STATUS_UPDATE_PORT = 2373

    def __init__(self, incremental_count_mode: bool = False):
        self._logger = logging.getLogger(__name__)
        self._logger.addHandler(logging.NullHandler())

        self.socket: Optional[socket.socket] = None
        self.port = None
        self.enabled = (
            os.environ.get(
                "PROC_WRAPPER_ENABLE_STATUS_UPDATE_LISTENER", "FALSE"
            ).upper()
            == "TRUE"
        )

        if self.enabled:
            self._logger.info("StatusUpdater is enabled")
        else:
            self._logger.info("StatusUpdater is disabled")
            return

        self.port = int(
            os.environ.get("PROC_WRAPPER_STATUS_UPDATE_SOCKET_PORT")
            or StatusUpdater.DEFAULT_STATUS_UPDATE_PORT
        )

        self.incremental_count_mode = incremental_count_mode
        self.success_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self.expected_count = 0

        atexit.register(_exit_handler, self)

    def __enter__(self):
        """Implement entrypoint for python with statement."""
        return self

    def __exit__(self, _type, _value, _traceback):
        """Implement exit point for python with statement."""
        self.shutdown()

    def send_update(
        self,
        success_count: Optional[int] = None,
        error_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
        expected_count: Optional[int] = None,
        last_status_message: Optional[str] = None,
        extra_props: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return

        status_hash: Dict[str, Any] = {}

        if self.incremental_count_mode:
            if success_count == 0:
                success_count = None

            if error_count == 0:
                error_count = None

            if skipped_count == 0:
                skipped_count = None

            if expected_count == 0:
                expected_count = None

        if success_count is not None:
            if self.incremental_count_mode:
                self.success_count += success_count
            else:
                self.success_count = success_count

            status_hash["success_count"] = self.success_count

        if error_count is not None:
            if self.incremental_count_mode:
                self.error_count += error_count
            else:
                self.error_count = error_count

            status_hash["error_count"] = self.error_count

        if skipped_count is not None:
            if self.incremental_count_mode:
                self.skipped_count += skipped_count
            else:
                self.skipped_count = skipped_count

            status_hash["skipped_count"] = self.skipped_count

        if expected_count is not None:
            if self.incremental_count_mode:
                self.expected_count += expected_count
            else:
                self.expected_count = expected_count

            status_hash["expected_count"] = self.expected_count

        if last_status_message:
            status_hash["last_status_message"] = last_status_message

        if extra_props:
            status_hash["other_runtime_metadata"] = extra_props

        if not status_hash:
            return

        message = (json.dumps(status_hash) + "\n").encode("UTF-8")

        try:
            self.reuse_or_create_socket().sendto(message, ("127.0.0.1", self.port))
        except Exception:
            self._logger.debug("Can't send status update, resetting socket")
            self.socket = None

    def shutdown(self) -> None:
        if self.socket:
            self._logger.info("Closing status update socket ...")
            try:
                self.socket.close()
                self._logger.info("Done closing status update socket.")
            finally:
                self.socket = None

    def reuse_or_create_socket(self) -> socket.socket:
        if not self.socket:
            self.socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
            self.socket.setblocking(False)

        return self.socket
