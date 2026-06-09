"""Golden Config Nornir dispatcher for Aruba AOS-CX.

Why this exists
---------------
Golden Config backup for AOS-CX used to run through the ``napalm-aruba-cx``
fork, which (a) opened a REST/pyaoscx session AND a Netmiko SSH session on every
device, and (b) pulled ``show running-config`` over Netmiko with **no
read_timeout**, so the config pull died on the 10 s Netmiko default whenever a
switch was slow — the dominant cause of the Aruba backup failures.

This dispatcher retires the fork from the Golden Config path entirely. It is a
thin subclass of nornir-nautobot's stock ``NetmikoDefault`` that:

  * issues ``show running-config`` (AOS-CX does not accept the ``show run``
    abbreviation that ``NetmikoDefault`` hard-codes), and
  * passes an explicit, generous ``read_timeout`` so large configs under
    concurrency don't trip the Netmiko default.

It produces CLI-format text, which is exactly what the ``aruba_aoscx`` intended
templates render against — so compliance compares like-for-like.

Wiring (in nautobot_config.py)
------------------------------
    NETWORK_DRIVERS = {"netmiko": {"aruba_aoscx": "aruba_aoscx"}}

    PLUGINS_CONFIG["nautobot_golden_config"]["custom_dispatcher"] = {
        "aruba_aoscx": "sohonet_nsot_helpers.golden_config.dispatcher.AOSCXNetmikoDispatcher",
    }

The ``NETWORK_DRIVERS`` override is required because netutils 1.8.1 has no
netmiko mapping for ``aruba_aoscx``; it tells the Nornir inventory to build the
Netmiko connection with ``device_type=aruba_aoscx`` (available since netmiko
4.4). ``custom_dispatcher`` forces this class for AOS-CX regardless of the
global backup framework.
"""
import os

from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_nautobot.exceptions import NornirNautobotException
from nornir_nautobot.plugins.tasks.dispatcher.default import NetmikoDefault
from nornir_nautobot.utils.helpers import make_folder

try:  # netmiko exception names are stable across 4.x
    from netmiko.exceptions import (
        NetmikoAuthenticationException,
        NetmikoTimeoutException,
        ReadTimeout,
    )
except ImportError:  # pragma: no cover - very old netmiko
    from netmiko.ssh_exception import (  # type: ignore
        NetmikoAuthenticationException,
        NetmikoTimeoutException,
    )
    ReadTimeout = ()  # type: ignore  # isinstance(..., ()) is always False

from netutils.config.clean import clean_config, sanitize_config
from nornir.core.exceptions import NornirSubTaskError


# Generous default; large AOS-CX running-configs under 20-way concurrency can
# take well over the 10 s Netmiko default to return. Tunable via env without a
# code change / rebuild.
DEFAULT_READ_TIMEOUT = int(os.getenv("AOSCX_BACKUP_READ_TIMEOUT", "120"))

# Two distinct failure modes, retried differently:
#   * ReadTimeout — the device is REACHABLE but slow to return the config.
#     Retrying (on a fresh session) genuinely helps, so retry generously.
#   * NetmikoTimeoutException — the TCP connection itself failed: the device is
#     unreachable (down / decommissioned / firewalled). It will not come back
#     mid-run, so retrying mostly just burns the connect timeout. Fail fast.
DEFAULT_MAX_RETRIES = int(os.getenv("AOSCX_BACKUP_MAX_RETRIES", "2"))
DEFAULT_CONNECT_RETRIES = int(os.getenv("AOSCX_BACKUP_CONNECT_RETRIES", "1"))


class AOSCXNetmikoDispatcher(NetmikoDefault):
    """Netmiko-based Golden Config dispatcher for Aruba AOS-CX."""

    # AOS-CX requires the full keyword; it does not accept "show run".
    config_command = "show running-config"

    # AOS-CX has no separate enable mode — manager context is entered at login.
    enable_default = False

    read_timeout = DEFAULT_READ_TIMEOUT

    # Retries AFTER the first attempt. read-timeouts (reachable, slow) retry
    # generously; connect-failures (unreachable) fail fast.
    max_retries = DEFAULT_MAX_RETRIES            # ReadTimeout / other
    connect_retries = DEFAULT_CONNECT_RETRIES    # NetmikoTimeoutException (connect)

    @classmethod
    def get_config(
        cls,
        task: Task,
        logger,
        obj,
        backup_file: str,
        remove_lines: list,
        substitute_lines: list,
    ) -> Result:
        """Get the running config over Netmiko with an explicit read_timeout.

        Mirrors ``NetmikoDefault.get_config`` but (1) sends the AOS-CX command,
        (2) supplies ``read_timeout`` so the pull survives slow/large configs,
        and (3) does not attempt enable mode.
        """
        logger.debug(
            f"Executing AOS-CX get_config for {task.host.name} "
            f"(read_timeout={cls.read_timeout}s, max_retries={cls.max_retries}, "
            f"connect_retries={cls.connect_retries})"
        )

        attempt = 0
        while True:
            try:
                result = task.run(
                    task=netmiko_send_command,
                    command_string=cls.config_command,
                    read_timeout=cls.read_timeout,
                    enable=cls.enable_default,
                )
                break
            except NornirSubTaskError as exc:
                exception = exc.result.exception

                # Authentication failures are not transient — fail fast.
                if isinstance(exception, NetmikoAuthenticationException):
                    error_msg = f"`E1017:` Failed with an authentication issue: `{exception}`"
                    logger.error(error_msg, extra={"object": obj})
                    raise NornirNautobotException(error_msg)

                # A connect failure (unreachable device) is treated as terminal
                # much sooner than a read-timeout (reachable but slow), because a
                # down device won't recover within the run. ReadTimeout is NOT a
                # subclass of NetmikoTimeoutException, so order the check so the
                # bare NetmikoTimeoutException == connect failure.
                is_read_timeout = bool(ReadTimeout) and isinstance(exception, ReadTimeout)
                is_connect_fail = isinstance(exception, NetmikoTimeoutException) and not is_read_timeout
                budget = cls.connect_retries if is_connect_fail else cls.max_retries

                if attempt < budget:
                    attempt += 1
                    logger.warning(
                        f"`W1018:` {task.host.name}: backup attempt {attempt}/{budget} "
                        f"after `{type(exception).__name__}`; reconnecting.",
                        extra={"object": obj},
                    )
                    try:
                        task.host.close_connection("netmiko")
                    except Exception:  # noqa: BLE001 - best-effort; a fresh open follows
                        pass
                    continue

                attempts = attempt + 1
                if is_connect_fail:
                    error_msg = (
                        f"`E1018:` Device unreachable — TCP connection failed after "
                        f"{attempts} attempt(s). `{exception}`"
                    )
                elif is_read_timeout:
                    error_msg = (
                        f"`E1018:` Read timed out after {attempts} attempt(s) — device slow or "
                        f"config very large; consider raising AOSCX_BACKUP_READ_TIMEOUT. `{exception}`"
                    )
                else:
                    error_msg = f"`E1016:` Failed with an unexpected issue after {attempts} attempt(s). `{exception}`"
                logger.error(error_msg, extra={"object": obj})
                raise NornirNautobotException(error_msg)

        if result[0].failed:
            return result

        running_config = result[0].result

        if remove_lines:
            running_config = clean_config(running_config, remove_lines)
        if substitute_lines:
            running_config = sanitize_config(running_config, substitute_lines)

        if backup_file:
            make_folder(os.path.dirname(backup_file))
            with open(backup_file, "w", encoding="utf8") as filehandler:
                filehandler.write(running_config)

        return Result(host=task.host, result={"config": running_config})
