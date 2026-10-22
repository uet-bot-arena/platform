"""Windows sandbox primitives: AppContainer with zero capabilities + Job Object.

No administrator rights, no WSL, no virtualization. Used only by the trusted supervisor on a
Windows grader. Each bot run gets its own AppContainer profile (own SID), so two bots on the
same machine cannot read each other's files. The toolchain is readable by every AppContainer
(ALL APPLICATION PACKAGES), which is harmless: it holds no secrets.
"""
import ctypes
import ctypes.wintypes as wt
import msvcrt
import os
import subprocess
import uuid
import winreg

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv")
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

ALL_APPLICATION_PACKAGES = "S-1-15-2-1"
PREFIX = "uet-arena-run-"
MIB = 1024 * 1024
INFINITE = 0xFFFFFFFF


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class EXT_LIMIT(ctypes.Structure):
    _fields_ = [("Basic", BASIC_LIMIT), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class ACCOUNTING(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD),
                ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wt.DWORD), ("Reserved", wt.DWORD)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wt.BOOL)]


k32.CreateJobObjectW.restype = wt.HANDLE
k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
k32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                               ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
                               ctypes.POINTER(PROCESS_INFORMATION)]
k32.CreatePipe.argtypes = [ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
                           ctypes.POINTER(SECURITY_ATTRIBUTES), wt.DWORD]
k32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
k32.ResumeThread.argtypes = [wt.HANDLE]
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.WaitForSingleObject.restype = wt.DWORD
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.SetErrorMode.argtypes = [wt.UINT]
userenv.CreateAppContainerProfile.restype = ctypes.c_long
userenv.CreateAppContainerProfile.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD,
                                              ctypes.POINTER(ctypes.c_void_p)]
userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
userenv.DeleteAppContainerProfile.restype = ctypes.c_long
userenv.DeleteAppContainerProfile.argtypes = [wt.LPCWSTR]
userenv.GetAppContainerFolderPath.restype = ctypes.c_long
userenv.GetAppContainerFolderPath.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
ole32 = ctypes.WinDLL("ole32")
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
advapi.FreeSid.argtypes = [ctypes.c_void_p]
ntdll.NtSuspendProcess.argtypes = [wt.HANDLE]
ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
k32.LocalFree.argtypes = [ctypes.c_void_p]


def check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def quiet_errors():
    """No crash or missing-file dialogs: a crashing bot must exit, not wait for a click."""
    k32.SetErrorMode(0x0001 | 0x0002 | 0x8000)


class Container:
    """One AppContainer profile; deleted on close."""

    def __init__(self, name=None):
        self.name = name or PREFIX + uuid.uuid4().hex[:16]
        self.sid = ctypes.c_void_p()
        hr = userenv.CreateAppContainerProfile(self.name, self.name, "UET Bot Arena bot sandbox", None, 0, ctypes.byref(self.sid))
        if hr == ctypes.c_long(0x800700B7).value:  # already exists
            hr = userenv.DeriveAppContainerSidFromAppContainerName(self.name, ctypes.byref(self.sid))
        if hr != 0:
            raise OSError(f"CreateAppContainerProfile failed: {hr & 0xFFFFFFFF:#x}")
        text = wt.LPWSTR()
        check(advapi.ConvertSidToStringSidW(self.sid, ctypes.byref(text)), "ConvertSidToStringSid")
        self.sid_text = text.value
        k32.LocalFree(text)
        # Windows points the bot's LOCALAPPDATA, TEMP and TMP at this folder and lets it write
        # there, so the supervisor counts it in the bot's disk quota together with the work dir.
        folder = ctypes.c_void_p()
        if userenv.GetAppContainerFolderPath(self.sid_text, ctypes.byref(folder)) == 0 and folder.value:
            self.folder = ctypes.wstring_at(folder.value)
            ole32.CoTaskMemFree(folder)
        else:
            self.folder = None

    def grant(self, path, rights):
        grant(path, self.sid_text, rights)

    def close(self):
        if self.sid:
            advapi.FreeSid(self.sid)
            self.sid = ctypes.c_void_p()
        userenv.DeleteAppContainerProfile(self.name)


def grant(path, sid_text, rights, recursive=True):
    """rights: RX (read+execute) or M (modify). Inheritable so new files get the same ACE."""
    argv = ["icacls", str(path), "/grant", f"*{sid_text}:(OI)(CI){rights}", "/Q"] + (["/T"] if recursive else [])
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=0x08000000)


def cleanup_profiles(prefix=PREFIX):
    """Delete AppContainer profiles left by a crashed supervisor."""
    key = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Mappings"
    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as mappings:
            for index in range(winreg.QueryInfoKey(mappings)[0]):
                try:
                    with winreg.OpenKey(mappings, winreg.EnumKey(mappings, index)) as item:
                        moniker = winreg.QueryValueEx(item, "Moniker")[0]
                except OSError:
                    continue
                if isinstance(moniker, str) and moniker.startswith(prefix):
                    names.append(moniker)
    except FileNotFoundError:
        return 0
    for name in names:
        userenv.DeleteAppContainerProfile(name)
    return len(names)


class Job:
    def __init__(self, process_limit=1, process_memory_mib=None, job_memory_mib=None, cpu=None, cpu_seconds=None):
        self.handle = k32.CreateJobObjectW(None, None)
        check(self.handle, "CreateJobObject")
        info = EXT_LIMIT()
        flags = 0x8 | 0x400 | 0x2000  # active process limit, die on unhandled exception, kill on job close
        info.Basic.ActiveProcessLimit = process_limit
        if cpu is not None:
            flags |= 0x10
            info.Basic.Affinity = 1 << cpu  # one logical CPU: extra threads give no speedup
        if process_memory_mib:
            flags |= 0x100
            info.ProcessMemoryLimit = process_memory_mib * MIB
        if job_memory_mib:
            flags |= 0x200
            info.JobMemoryLimit = job_memory_mib * MIB
        if cpu_seconds:
            flags |= 0x2
            info.Basic.PerProcessUserTimeLimit = int(cpu_seconds * 10_000_000)
        info.Basic.LimitFlags = flags
        check(k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)), "job limits")
        ui = wt.DWORD(0xFF)  # no clipboard, desktop, display settings, global atoms, user handles, exit windows
        check(k32.SetInformationJobObject(self.handle, 4, ctypes.byref(ui), ctypes.sizeof(ui)), "job UI limits")

    def assign(self, process):
        check(k32.AssignProcessToJobObject(self.handle, process), "AssignProcessToJobObject")

    def accounting(self):
        if not self.handle:
            return {}
        basic, ext = ACCOUNTING(), EXT_LIMIT()
        k32.QueryInformationJobObject(self.handle, 1, ctypes.byref(basic), ctypes.sizeof(basic), None)
        k32.QueryInformationJobObject(self.handle, 9, ctypes.byref(ext), ctypes.sizeof(ext), None)
        return {"cpu_usage_usec": (basic.TotalUserTime + basic.TotalKernelTime) // 10,
                "memory_peak_bytes": ext.PeakJobMemoryUsed, "process_peak_bytes": ext.PeakProcessMemoryUsed,
                "active_processes": basic.ActiveProcesses, "total_processes": basic.TotalProcesses}

    def terminate(self, code=1):
        if self.handle:
            k32.TerminateJobObject(self.handle, code)

    def close(self):
        if self.handle:
            k32.CloseHandle(self.handle)  # KILL_ON_JOB_CLOSE ends anything still running
            self.handle = None


def _pipe():
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    read, write = wt.HANDLE(), wt.HANDLE()
    check(k32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(sa), 1 << 16), "CreatePipe")
    return read, write


def _environment(env):
    block = "".join(f"{k}={v}\0" for k, v in sorted(env.items())) + "\0"
    return ctypes.create_unicode_buffer(block)


class Process:
    """A process inside ``container`` and ``job``; stdio are anonymous pipes owned by the caller."""

    def __init__(self, argv, cwd, env, container, job, merge_stderr=False):
        child_in, self._in = _pipe()
        self._out, child_out = _pipe()
        if merge_stderr:
            self._err, child_err = None, child_out
        else:
            self._err, child_err = _pipe()
        for parent_end in filter(None, (self._in, self._out, self._err)):
            k32.SetHandleInformation(parent_end, 1, 0)  # never inherited
        inherited = [child_in, child_out] + ([] if merge_stderr else [child_err])
        handles = (wt.HANDLE * len(inherited))(*inherited)
        size = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        check(k32.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)), "attribute list")
        try:
            caps = SECURITY_CAPABILITIES(container.sid, None, 0, 0)  # zero capabilities: no network, no devices
            check(k32.UpdateProcThreadAttribute(attributes, 0, 0x20009, ctypes.byref(caps), ctypes.sizeof(caps), None, None),
                  "security capabilities")
            # Only the three stdio pipes are inherited, never the supervisor's other handles.
            check(k32.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, ctypes.sizeof(handles), None, None),
                  "handle list")
            info = STARTUPINFOEXW()
            info.StartupInfo.cb = ctypes.sizeof(info)
            info.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            info.StartupInfo.hStdInput, info.StartupInfo.hStdOutput, info.StartupInfo.hStdError = child_in, child_out, child_err
            info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            process = PROCESS_INFORMATION()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
            # EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
            check(k32.CreateProcessW(str(argv[0]), command, None, None, True, 0x80000 | 0x4 | 0x400 | 0x08000000,
                                     _environment(env), str(cwd), ctypes.byref(info), ctypes.byref(process)), "CreateProcess")
        finally:
            k32.DeleteProcThreadAttributeList(attributes)
            for handle in {child_in.value, child_out.value, child_err.value}:
                k32.CloseHandle(handle)
        self.handle, self.pid = process.hProcess, process.dwProcessId
        try:
            job.assign(self.handle)  # before the first instruction runs
        except OSError:
            k32.TerminateProcess(self.handle, 1)
            k32.CloseHandle(process.hThread)
            raise
        k32.ResumeThread(process.hThread)
        k32.CloseHandle(process.hThread)
        self.stdin = os.fdopen(msvcrt.open_osfhandle(self._in.value, 0), "wb", buffering=0)
        self.stdout = os.fdopen(msvcrt.open_osfhandle(self._out.value, os.O_RDONLY), "rb", buffering=0)
        self.stderr = os.fdopen(msvcrt.open_osfhandle(self._err.value, os.O_RDONLY), "rb", buffering=0) if self._err else None
        self.returncode = None

    def suspend(self):
        ntdll.NtSuspendProcess(self.handle)

    def resume(self):
        ntdll.NtResumeProcess(self.handle)

    def poll(self):
        if self.returncode is None and self.handle and k32.WaitForSingleObject(self.handle, 0) == 0:
            code = wt.DWORD()
            k32.GetExitCodeProcess(self.handle, ctypes.byref(code))
            self.returncode = code.value
        return self.returncode

    def wait(self, seconds):
        if self.handle and k32.WaitForSingleObject(self.handle, int(seconds * 1000)) == 0:
            return self.poll()
        return None

    def kill(self):
        if self.handle and self.poll() is None:
            k32.TerminateProcess(self.handle, 0xC000013A)  # STATUS_CONTROL_C_EXIT, never a valid bot exit

    def close(self):
        for stream in (self.stdin, self.stdout, self.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        if self.handle:
            self.poll()
            k32.CloseHandle(self.handle)
            self.handle = None

