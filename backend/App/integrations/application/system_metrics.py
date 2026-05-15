from __future__ import annotations

import os
import time
from typing import Any, Optional

_psutil: Optional[Any] = None
try:
    import psutil as _psutil_import
    _psutil = _psutil_import
except Exception:
    pass

_pynvml: Optional[Any] = None
_nvml_init_ok: Optional[bool] = None
_nvidia_smi_path: Optional[str] = None
_gpu_disabled_reason: Optional[str] = None

try:
    import pynvml as _pynvml_import
    _pynvml = _pynvml_import
except Exception:
    pass


def _ensure_nvml() -> bool:
    global _nvml_init_ok, _gpu_disabled_reason
    if _nvml_init_ok is not None:
        return _nvml_init_ok
    if _pynvml is None:
        _nvml_init_ok = False
        return False
    try:
        _pynvml.nvmlInit()
        _nvml_init_ok = True
        return True
    except Exception as exc:
        _nvml_init_ok = False
        _gpu_disabled_reason = f"nvml_init_failed: {exc}"
        return False


def _gpu_from_nvml() -> Optional[dict[str, Any]]:
    if not _ensure_nvml():
        return None
    try:
        count = _pynvml.nvmlDeviceGetCount()
        if count <= 0:
            return None
        utils: list[float] = []
        mem_used = 0
        mem_total = 0
        name = None
        for idx in range(count):
            handle = _pynvml.nvmlDeviceGetHandleByIndex(idx)
            util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = _pynvml.nvmlDeviceGetMemoryInfo(handle)
            utils.append(float(util.gpu))
            mem_used += int(mem.used)
            mem_total += int(mem.total)
            if name is None:
                try:
                    raw = _pynvml.nvmlDeviceGetName(handle)
                    name = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                except Exception:
                    name = "GPU"
        gpu_percent = sum(utils) / len(utils) if utils else 0.0
        mem_percent = (mem_used / mem_total * 100.0) if mem_total else None
        result: dict[str, Any] = {
            "gpu_percent": round(gpu_percent, 1),
            "gpu_count": count,
            "gpu_source": "nvml",
        }
        if name:
            result["gpu_name"] = name
        if mem_percent is not None:
            result["gpu_memory_percent"] = round(mem_percent, 1)
            result["gpu_memory_used_gb"] = round(mem_used / (1024**3), 2)
            result["gpu_memory_total_gb"] = round(mem_total / (1024**3), 2)
        return result
    except Exception as exc:
        global _gpu_disabled_reason
        _gpu_disabled_reason = f"nvml_sample_failed: {exc}"
        return None


def _resolve_nvidia_smi() -> Optional[str]:
    global _nvidia_smi_path
    if _nvidia_smi_path is not None:
        return _nvidia_smi_path or None
    from shutil import which
    found = which("nvidia-smi")
    _nvidia_smi_path = found or ""
    return found


def _gpu_from_nvidia_smi() -> Optional[dict[str, Any]]:
    smi = _resolve_nvidia_smi()
    if not smi:
        return None
    import subprocess
    try:
        out = subprocess.run(
            [
                smi,
                "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        utils: list[float] = []
        mem_used_mib = 0
        mem_total_mib = 0
        name: Optional[str] = None
        count = 0
        for raw_line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in raw_line.split(",")]
            if len(parts) < 4:
                continue
            try:
                utils.append(float(parts[0]))
                mem_used_mib += int(float(parts[1]))
                mem_total_mib += int(float(parts[2]))
            except ValueError:
                continue
            if name is None:
                name = parts[3]
            count += 1
        if count == 0:
            return None
        gpu_percent = sum(utils) / len(utils) if utils else 0.0
        mem_percent = (mem_used_mib / mem_total_mib * 100.0) if mem_total_mib else None
        result: dict[str, Any] = {
            "gpu_percent": round(gpu_percent, 1),
            "gpu_count": count,
            "gpu_source": "nvidia-smi",
        }
        if name:
            result["gpu_name"] = name
        if mem_percent is not None:
            result["gpu_memory_percent"] = round(mem_percent, 1)
            result["gpu_memory_used_gb"] = round(mem_used_mib / 1024, 2)
            result["gpu_memory_total_gb"] = round(mem_total_mib / 1024, 2)
        return result
    except Exception as exc:
        global _gpu_disabled_reason
        _gpu_disabled_reason = f"nvidia_smi_failed: {exc}"
        global _nvidia_smi_path
        _nvidia_smi_path = ""
        return None


def gpu_metrics() -> Optional[dict[str, Any]]:
    if _gpu_disabled_reason and _nvml_init_ok is False and _nvidia_smi_path == "":
        return None
    snap = _gpu_from_nvml()
    if snap is not None:
        return snap
    return _gpu_from_nvidia_smi()


def metrics_payload() -> dict[str, Any]:
    data: dict[str, Any] = {
        "provider": "local",
        "timestamp_ms": int(time.time() * 1000),
    }
    try:
        if _psutil is None:
            data["loadavg"] = os.getloadavg()
        else:
            data["cpu_percent"] = _psutil.cpu_percent(interval=0.1)
            vm = _psutil.virtual_memory()
            data["memory_percent"] = vm.percent
            data["memory_used_gb"] = round(vm.used / (1024**3), 2)
            data["memory_total_gb"] = round(vm.total / (1024**3), 2)
    except Exception as exc:
        data["error"] = str(exc)
    gpu = gpu_metrics()
    if gpu is not None:
        data.update(gpu)
    return data


def task_snapshot(task_store: Any, task_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not task_id:
        return None
    try:
        return task_store.get_task(task_id)
    except KeyError:
        return {"error": "not_found", "task_id": task_id}


def build_live_tick_payload(task_store: Any, task_id: Optional[str]) -> dict[str, Any]:
    from backend.App.workspace.infrastructure.workspace_io import (
        command_exec_allowed,
        workspace_write_allowed,
    )
    return {
        "type": "tick",
        "metrics": metrics_payload(),
        "task": task_snapshot(task_store, task_id),
        "capabilities": {
            "workspace_write": workspace_write_allowed(),
            "command_exec": command_exec_allowed(),
        },
    }
