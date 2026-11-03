"""Native WASAPI process-loopback capture for Windows 10 2004 and newer.

The regular PyAudioWPatch loopback source captures an entire output endpoint,
including audio rendered by this process.  Windows' process-loopback virtual
device can instead capture every process *except* this process tree.  That
keeps translated playback audible without feeding it back into Gemini.

The API is loaded dynamically and activation is treated as the final feature
test.  Importing this module therefore remains safe on older Windows builds.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from typing import Callable, Optional


MIN_PROCESS_LOOPBACK_BUILD = 19041  # Windows 10 version 2004
PROCESS_LOOPBACK_SAMPLE_RATE = 48000
PROCESS_LOOPBACK_CHANNELS = 2

_S_OK = 0
_S_FALSE = 1
_E_FAIL = ctypes.c_int32(0x80004005).value
_E_NOINTERFACE = ctypes.c_int32(0x80004002).value
_E_UNEXPECTED = ctypes.c_int32(0x8000FFFF).value
_RPC_E_CHANGED_MODE = ctypes.c_int32(0x80010106).value

_COINIT_MULTITHREADED = 0
_WAVE_FORMAT_IEEE_FLOAT = 3
_AUDCLNT_SHAREMODE_SHARED = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
_AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002

_AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
_PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
_PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
_VT_BLOB = 65
_VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

HRESULT = ctypes.c_int32
ULONG = ctypes.c_uint32
DWORD = ctypes.c_uint32
UINT32 = ctypes.c_uint32
UINT64 = ctypes.c_uint64
BYTE = ctypes.c_ubyte
WORD = ctypes.c_uint16
WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


_IID_IUNKNOWN = _guid("00000000-0000-0000-C000-000000000046")
_IID_IAGILE_OBJECT = _guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
_IID_IACTIVATE_AUDIO_INTERFACE_COMPLETION_HANDLER = _guid(
    "41D949AB-9862-444A-80F6-C261334DA5EB"
)
_IID_IAUDIO_CLIENT = _guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
_IID_IAUDIO_CAPTURE_CLIENT = _guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")


class _AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ("TargetProcessId", DWORD),
        ("ProcessLoopbackMode", ctypes.c_int32),
    ]


class _AUDIOCLIENT_ACTIVATION_UNION(ctypes.Union):
    _fields_ = [("ProcessLoopbackParams", _AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


class _AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _anonymous_ = ("Details",)
    _fields_ = [
        ("ActivationType", ctypes.c_int32),
        ("Details", _AUDIOCLIENT_ACTIVATION_UNION),
    ]


class _BLOB(ctypes.Structure):
    _fields_ = [("cbSize", ULONG), ("pBlobData", ctypes.POINTER(BYTE))]


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [("blob", _BLOB), ("_alignment", ctypes.c_void_p * 2)]


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", WORD),
        ("wReserved1", WORD),
        ("wReserved2", WORD),
        ("wReserved3", WORD),
        ("value", _PROPVARIANT_VALUE),
    ]


class _WAVEFORMATEX(ctypes.Structure):
    # WAVEFORMATEX is an 18-byte, 2-byte-packed Windows multimedia structure.
    _pack_ = 2
    _fields_ = [
        ("wFormatTag", WORD),
        ("nChannels", WORD),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", WORD),
        ("wBitsPerSample", WORD),
        ("cbSize", WORD),
    ]


class _OSVERSIONINFOEXW(ctypes.Structure):
    _fields_ = [
        ("dwOSVersionInfoSize", DWORD),
        ("dwMajorVersion", DWORD),
        ("dwMinorVersion", DWORD),
        ("dwBuildNumber", DWORD),
        ("dwPlatformId", DWORD),
        ("szCSDVersion", ctypes.c_wchar * 128),
        ("wServicePackMajor", WORD),
        ("wServicePackMinor", WORD),
        ("wSuiteMask", WORD),
        ("wProductType", BYTE),
        ("wReserved", BYTE),
    ]


@dataclass(frozen=True)
class ProcessLoopbackCapability:
    """Cheap preflight result; successful activation remains authoritative."""

    available: bool
    reason: str = ""
    windows_build: Optional[int] = None


class ProcessLoopbackUnavailable(RuntimeError):
    """Raised when native self-excluding loopback cannot be activated."""

    def __init__(self, message: str, hresult: Optional[int] = None) -> None:
        self.hresult = hresult
        if hresult is not None:
            message = f"{message} (HRESULT 0x{hresult & 0xFFFFFFFF:08X})"
        super().__init__(message)


def _failed(hr: int) -> bool:
    return ctypes.c_int32(hr).value < 0


def _windows_build_number() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        ntdll = ctypes.WinDLL("ntdll.dll")
        rtl_get_version = ntdll.RtlGetVersion
        rtl_get_version.argtypes = [ctypes.POINTER(_OSVERSIONINFOEXW)]
        rtl_get_version.restype = HRESULT
        version = _OSVERSIONINFOEXW()
        version.dwOSVersionInfoSize = ctypes.sizeof(version)
        if _failed(rtl_get_version(ctypes.byref(version))):
            return None
        return int(version.dwBuildNumber)
    except (AttributeError, OSError):
        return None


_activation_loader_lock = threading.Lock()
_mmdevapi = None
_activate_audio_interface_async = None


def _load_activation_function():
    """Resolve the API at runtime so unsupported systems can still launch."""
    global _mmdevapi, _activate_audio_interface_async
    with _activation_loader_lock:
        if _activate_audio_interface_async is not None:
            return _activate_audio_interface_async
        if sys.platform != "win32":
            raise ProcessLoopbackUnavailable("Native process loopback requires Windows")
        try:
            _mmdevapi = ctypes.WinDLL("Mmdevapi.dll")
            function = _mmdevapi.ActivateAudioInterfaceAsync
        except (AttributeError, OSError) as exc:
            raise ProcessLoopbackUnavailable(
                "ActivateAudioInterfaceAsync is not available"
            ) from exc
        function.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(_PROPVARIANT),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        function.restype = HRESULT
        _activate_audio_interface_async = function
        return function


def get_process_loopback_capability() -> ProcessLoopbackCapability:
    """Return whether this OS is a candidate for process-loopback activation.

    OBS enables this API from build 19041 despite Microsoft's conservative
    build-20348 documentation.  We use the same floor, then still require the
    function to exist and the real activation to succeed.
    """
    if sys.platform != "win32":
        return ProcessLoopbackCapability(
            False, "Native process loopback requires Windows"
        )
    build = _windows_build_number()
    if build is not None and build < MIN_PROCESS_LOOPBACK_BUILD:
        return ProcessLoopbackCapability(
            False,
            (
                "Native process exclusion requires Windows 10 version 2004 "
                f"(build {MIN_PROCESS_LOOPBACK_BUILD}) or newer"
            ),
            build,
        )
    try:
        _load_activation_function()
    except ProcessLoopbackUnavailable as exc:
        return ProcessLoopbackCapability(False, str(exc), build)
    return ProcessLoopbackCapability(True, windows_build=build)


_QueryInterface = WINFUNCTYPE(
    HRESULT,
    ctypes.c_void_p,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(ctypes.c_void_p),
)
_AddRef = WINFUNCTYPE(ULONG, ctypes.c_void_p)
_Release = WINFUNCTYPE(ULONG, ctypes.c_void_p)
_ActivateCompleted = WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)


class _CompletionHandlerVTable(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _QueryInterface),
        ("AddRef", _AddRef),
        ("Release", _Release),
        ("ActivateCompleted", _ActivateCompleted),
    ]


class _CompletionHandlerInterface(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_CompletionHandlerVTable))]


def _guid_bytes(value: _GUID) -> bytes:
    return ctypes.string_at(ctypes.byref(value), ctypes.sizeof(value))


_SUPPORTED_HANDLER_IIDS = {
    _guid_bytes(_IID_IUNKNOWN),
    _guid_bytes(_IID_IAGILE_OBJECT),
    _guid_bytes(_IID_IACTIVATE_AUDIO_INTERFACE_COMPLETION_HANDLER),
}


def _com_method(pointer: ctypes.c_void_p, index: int, restype, *argtypes):
    if not pointer or not pointer.value:
        raise ProcessLoopbackUnavailable("Received a null COM interface")
    vtable = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    address = vtable[index]
    return WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(address)


def _com_release(pointer: Optional[ctypes.c_void_p]) -> None:
    if pointer is None or not pointer.value:
        return
    try:
        _com_method(pointer, 2, ULONG)(pointer)
    except Exception:
        traceback.print_exc()


class _ActivationCompletionHandler:
    """Small agile COM callback used by ActivateAudioInterfaceAsync."""

    def __init__(self) -> None:
        self.completed = threading.Event()
        self.activation_hresult = _E_UNEXPECTED
        self.audio_client = ctypes.c_void_p()
        self._ref_count = 1
        self._ref_lock = threading.Lock()

        self._query_interface_callback = _QueryInterface(self._query_interface)
        self._add_ref_callback = _AddRef(self._add_ref)
        self._release_callback = _Release(self._release)
        self._activate_completed_callback = _ActivateCompleted(
            self._activate_completed
        )
        self._vtable = _CompletionHandlerVTable(
            self._query_interface_callback,
            self._add_ref_callback,
            self._release_callback,
            self._activate_completed_callback,
        )
        self._interface = _CompletionHandlerInterface(ctypes.pointer(self._vtable))

    @property
    def pointer(self) -> ctypes.c_void_p:
        return ctypes.cast(ctypes.pointer(self._interface), ctypes.c_void_p)

    def _query_interface(self, this, iid, result) -> int:
        try:
            if not result:
                return _E_NOINTERFACE
            result[0] = None
            if iid and ctypes.string_at(iid, ctypes.sizeof(_GUID)) in (
                _SUPPORTED_HANDLER_IIDS
            ):
                result[0] = self.pointer.value
                self._add_ref(this)
                return _S_OK
        except Exception:
            traceback.print_exc()
        return _E_NOINTERFACE

    def _add_ref(self, this) -> int:
        with self._ref_lock:
            self._ref_count += 1
            return self._ref_count

    def _release(self, this) -> int:
        with self._ref_lock:
            self._ref_count = max(0, self._ref_count - 1)
            return self._ref_count

    def _activate_completed(self, this, operation_value) -> int:
        try:
            operation = ctypes.c_void_p(operation_value)
            activation_hresult = HRESULT(_E_UNEXPECTED)
            audio_client = ctypes.c_void_p()
            get_result = _com_method(
                operation,
                3,
                HRESULT,
                ctypes.POINTER(HRESULT),
                ctypes.POINTER(ctypes.c_void_p),
            )
            call_hresult = get_result(
                operation,
                ctypes.byref(activation_hresult),
                ctypes.byref(audio_client),
            )
            self.activation_hresult = (
                int(call_hresult)
                if _failed(call_hresult)
                else int(activation_hresult.value)
            )
            if not _failed(self.activation_hresult):
                self.audio_client = audio_client
        except Exception:
            self.activation_hresult = _E_FAIL
            traceback.print_exc()
        finally:
            self.completed.set()
        return _S_OK


_pending_activation_lock = threading.Lock()
_pending_activations: list[tuple] = []


def _retain_timed_out_activation(objects: tuple) -> None:
    """Keep async callback memory alive if Windows fails to answer promptly."""
    handler, _params, _variant, operation = objects
    with _pending_activation_lock:
        _pending_activations.append(objects)

    def cleanup() -> None:
        handler.completed.wait()
        _com_release(operation)
        with _pending_activation_lock:
            try:
                _pending_activations.remove(objects)
            except ValueError:
                pass

    threading.Thread(target=cleanup, daemon=True).start()


def _make_activation_params(target_process_id: int, include: bool = False) -> _AUDIOCLIENT_ACTIVATION_PARAMS:
    params = _AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = _AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = target_process_id
    params.ProcessLoopbackParams.ProcessLoopbackMode = (
        _PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if include
        else _PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
    )
    return params


def _activate_process_loopback(timeout: float = 5.0, target_pid: int | None = None,
                               include: bool = False) -> ctypes.c_void_p:
    capability = get_process_loopback_capability()
    if not capability.available:
        raise ProcessLoopbackUnavailable(capability.reason)

    activation_params = _make_activation_params(
        target_pid if target_pid is not None else os.getpid(), include)

    variant = _PROPVARIANT()
    variant.vt = _VT_BLOB
    variant.blob.cbSize = ctypes.sizeof(activation_params)
    variant.blob.pBlobData = ctypes.cast(
        ctypes.pointer(activation_params), ctypes.POINTER(BYTE)
    )

    handler = _ActivationCompletionHandler()
    operation = ctypes.c_void_p()
    activate = _load_activation_function()
    result = activate(
        _VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        ctypes.byref(_IID_IAUDIO_CLIENT),
        ctypes.byref(variant),
        handler.pointer,
        ctypes.byref(operation),
    )
    if _failed(result):
        raise ProcessLoopbackUnavailable(
            "Process-loopback activation request failed", int(result)
        )

    if not handler.completed.wait(timeout):
        _retain_timed_out_activation(
            (handler, activation_params, variant, operation)
        )
        raise ProcessLoopbackUnavailable("Process-loopback activation timed out")

    _com_release(operation)
    if _failed(handler.activation_hresult) or not handler.audio_client.value:
        raise ProcessLoopbackUnavailable(
            "Process-loopback activation failed", handler.activation_hresult
        )
    return handler.audio_client


def _initialize_com_mta() -> bool:
    ole32 = ctypes.WinDLL("ole32.dll")
    initialize = ole32.CoInitializeEx
    initialize.argtypes = [ctypes.c_void_p, DWORD]
    initialize.restype = HRESULT
    result = int(initialize(None, _COINIT_MULTITHREADED))
    if result in (_S_OK, _S_FALSE):
        return True
    if result == _RPC_E_CHANGED_MODE:
        return False
    raise ProcessLoopbackUnavailable("Could not initialize COM", result)


def _uninitialize_com() -> None:
    try:
        ctypes.WinDLL("ole32.dll").CoUninitialize()
    except Exception:
        traceback.print_exc()


class ProcessLoopbackCapture:
    """Capture the system mix while excluding this process and its children."""

    def __init__(
        self,
        on_float32_data: Callable[[bytes, int, int], None],
        sample_rate: int = PROCESS_LOOPBACK_SAMPLE_RATE,
        channels: int = PROCESS_LOOPBACK_CHANNELS,
    ) -> None:
        self.on_float32_data = on_float32_data
        self.sample_rate = sample_rate
        self.channels = channels
        self.target_pid: int | None = None
        self.include: bool = False
        self._block_align = channels * ctypes.sizeof(ctypes.c_float)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._startup_error: Optional[BaseException] = None
        self._runtime_error: Optional[BaseException] = None
        self._silent_buffers: dict[int, bytes] = {}

    def start(self, timeout: float = 7.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        self._runtime_error = None
        self._thread = threading.Thread(
            target=self._capture_thread,
            name="process-loopback-capture",
            daemon=True,
        )
        self._thread.start()
        if not self._ready_event.wait(timeout):
            self._stop_event.set()
            raise ProcessLoopbackUnavailable("Process-loopback startup timed out")
        if self._startup_error is not None:
            error = self._startup_error
            self._thread.join(timeout=1.0)
            self._thread = None
            if isinstance(error, ProcessLoopbackUnavailable):
                raise error
            raise ProcessLoopbackUnavailable(str(error)) from error

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is None or not thread.is_alive():
            self._thread = None

    @property
    def runtime_error(self) -> Optional[BaseException]:
        return self._runtime_error

    def _capture_thread(self) -> None:
        com_initialized = False
        audio_client = ctypes.c_void_p()
        capture_client = ctypes.c_void_p()
        started = False
        ready = False
        try:
            com_initialized = _initialize_com_mta()
            audio_client = _activate_process_loopback(
                target_pid=self.target_pid, include=self.include)

            wave_format = _WAVEFORMATEX()
            wave_format.wFormatTag = _WAVE_FORMAT_IEEE_FLOAT
            wave_format.nChannels = self.channels
            wave_format.nSamplesPerSec = self.sample_rate
            wave_format.wBitsPerSample = 32
            wave_format.nBlockAlign = self._block_align
            wave_format.nAvgBytesPerSec = self.sample_rate * self._block_align
            wave_format.cbSize = 0

            flags = (
                _AUDCLNT_STREAMFLAGS_LOOPBACK
                | _AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                | _AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            )
            initialize = _com_method(
                audio_client,
                3,
                HRESULT,
                ctypes.c_int32,
                DWORD,
                ctypes.c_int64,
                ctypes.c_int64,
                ctypes.POINTER(_WAVEFORMATEX),
                ctypes.POINTER(_GUID),
            )
            result = initialize(
                audio_client,
                _AUDCLNT_SHAREMODE_SHARED,
                flags,
                0,
                0,
                ctypes.byref(wave_format),
                None,
            )
            if _failed(result):
                raise ProcessLoopbackUnavailable(
                    "Could not initialize the process-loopback audio client",
                    int(result),
                )

            get_service = _com_method(
                audio_client,
                14,
                HRESULT,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            result = get_service(
                audio_client,
                ctypes.byref(_IID_IAUDIO_CAPTURE_CLIENT),
                ctypes.byref(capture_client),
            )
            if _failed(result):
                raise ProcessLoopbackUnavailable(
                    "Could not obtain the process-loopback capture client",
                    int(result),
                )

            start = _com_method(audio_client, 10, HRESULT)
            result = start(audio_client)
            if _failed(result):
                raise ProcessLoopbackUnavailable(
                    "Could not start process-loopback capture", int(result)
                )
            started = True
            ready = True
            self._ready_event.set()

            get_next_packet_size = _com_method(
                capture_client, 5, HRESULT, ctypes.POINTER(UINT32)
            )
            get_buffer = _com_method(
                capture_client,
                3,
                HRESULT,
                ctypes.POINTER(ctypes.POINTER(BYTE)),
                ctypes.POINTER(UINT32),
                ctypes.POINTER(DWORD),
                ctypes.POINTER(UINT64),
                ctypes.POINTER(UINT64),
            )
            release_buffer = _com_method(capture_client, 4, HRESULT, UINT32)
            while not self._stop_event.wait(0.01):
                self._drain_packets(
                    capture_client,
                    get_next_packet_size,
                    get_buffer,
                    release_buffer,
                )
        except BaseException as exc:
            if ready:
                self._runtime_error = exc
                traceback.print_exc()
            else:
                self._startup_error = exc
        finally:
            if started and audio_client.value:
                try:
                    stop = _com_method(audio_client, 11, HRESULT)
                    stop(audio_client)
                except Exception:
                    traceback.print_exc()
            _com_release(capture_client)
            _com_release(audio_client)
            if com_initialized:
                _uninitialize_com()
            self._ready_event.set()

    def _drain_packets(
        self,
        capture_client: ctypes.c_void_p,
        get_next_packet_size,
        get_buffer,
        release_buffer,
    ) -> None:
        while True:
            frames_available = UINT32()
            result = get_next_packet_size(
                capture_client, ctypes.byref(frames_available)
            )
            if _failed(result):
                raise ProcessLoopbackUnavailable(
                    "Could not query the process-loopback buffer", int(result)
                )
            if frames_available.value == 0:
                return

            data = ctypes.POINTER(BYTE)()
            frames = UINT32()
            flags = DWORD()
            device_position = UINT64()
            qpc_position = UINT64()
            result = get_buffer(
                capture_client,
                ctypes.byref(data),
                ctypes.byref(frames),
                ctypes.byref(flags),
                ctypes.byref(device_position),
                ctypes.byref(qpc_position),
            )
            if _failed(result):
                raise ProcessLoopbackUnavailable(
                    "Could not read the process-loopback buffer", int(result)
                )

            try:
                byte_count = int(frames.value) * self._block_align
                if flags.value & _AUDCLNT_BUFFERFLAGS_SILENT or not data:
                    payload = self._silent_buffers.get(byte_count)
                    if payload is None:
                        payload = bytes(byte_count)
                        self._silent_buffers[byte_count] = payload
                else:
                    payload = ctypes.string_at(data, byte_count)
                if payload:
                    self.on_float32_data(
                        payload, self.sample_rate, self.channels
                    )
            finally:
                release_result = release_buffer(capture_client, frames)
                if _failed(release_result):
                    raise ProcessLoopbackUnavailable(
                        "Could not release the process-loopback buffer",
                        int(release_result),
                    )
