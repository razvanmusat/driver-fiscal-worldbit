"""Port serial cu citire ȘI scriere, doar din biblioteca standard (agentul nu are dependențe): Windows prin ctypes (kernel32), restul prin termios.
Folosit de modulul POS: viteză, biți de date, paritate și stop se setează explicit, iar citirea are timeout.

`SerialPort(port, baud, bytesize, parity, stopbits)`: `open()`, `close()`, `write(date)`, `read(timeout)` (așteaptă cel mult `timeout` secunde primii
octeți și întoarce ce a venit; `b""` la timeout), `purge()` (golește ce a rămas necitit). Orice problemă a portului ridică `SerialError`."""
import os
import re
import select
import sys
import time

_COM = re.compile(r"^COM\d{1,3}$", re.IGNORECASE)
BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)


class SerialError(OSError):
    """Portul nu există, e ocupat sau nu răspunde."""


def device_path(port):
    """COM4 -> \\\\.\\COM4 (prefixul e obligatoriu pentru COM10+ și merge și pentru COM1-9); orice altceva rămâne cale de fișier (teste, Linux)."""
    port = (port or "").strip()
    return "\\\\.\\" + port.upper() if _COM.match(port) else port


def valid_port_name(port):
    """COMn (Windows) sau o cale absolută (Linux: /dev/ttyUSB0)."""
    port = (port or "").strip()
    return bool(_COM.match(port)) or (port.startswith("/") and not re.search(r"[\x00-\x1f\x7f]", port))


class SerialPort:
    def __init__(self, port, baud=9600, bytesize=7, parity="E", stopbits=1):
        parity = str(parity).upper()
        if baud not in BAUDS:
            raise ValueError(f"viteză neacceptată: {baud}")
        if bytesize not in (7, 8):
            raise ValueError("biți de date: 7 sau 8")
        if parity not in ("N", "E", "O"):
            raise ValueError("paritate: N, E sau O")
        if stopbits not in (1, 2):
            raise ValueError("biți de stop: 1 sau 2")
        self.port, self.baud, self.bytesize, self.parity, self.stopbits = (port or "").strip(), baud, bytesize, parity, stopbits
        self._impl = None

    def open(self):
        if not self.port:
            raise SerialError("Portul nu e setat")
        impl = (_WinPort if sys.platform == "win32" else _PosixPort)(self)
        impl.open()
        self._impl = impl

    def close(self):
        impl, self._impl = self._impl, None
        if impl is not None:
            impl.close()

    def write(self, data):
        self._need_open().write(bytes(data))

    def read(self, timeout):
        return self._need_open().read(max(0.0, float(timeout)))

    def purge(self):
        self._need_open().purge()

    def _need_open(self):
        if self._impl is None:
            raise SerialError("Portul nu e deschis")
        return self._impl

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


def _mesaj(port, cod_sau_eroare):
    if cod_sau_eroare in (2, 3) or isinstance(cod_sau_eroare, FileNotFoundError):
        return f"Portul {port} nu există (POS-ul e conectat prin USB și interfața e activată pe terminal?)"
    if cod_sau_eroare == 5 or isinstance(cod_sau_eroare, PermissionError):
        return f"Portul {port} e folosit de alt program sau nu are drepturi de acces"
    return f"Portul {port}: {cod_sau_eroare}"


# ---------------------------------------------------------------------------------------------------------- POSIX
class _PosixPort:
    def __init__(self, cfg):
        self.cfg, self.fd = cfg, None

    def open(self):
        import termios
        c = self.cfg
        path = device_path(c.port)
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise SerialError(_mesaj(c.port, e)) from e
        try:
            iflag, oflag, cflag, lflag, _, _, cc = termios.tcgetattr(fd)
            iflag, oflag, lflag = termios.IGNPAR, 0, 0
            cflag &= ~(termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
            cflag |= termios.CREAD | termios.CLOCAL | (termios.CS7 if c.bytesize == 7 else termios.CS8)
            if c.parity != "N":
                cflag |= termios.PARENB | (termios.PARODD if c.parity == "O" else 0)
            if c.stopbits == 2:
                cflag |= termios.CSTOPB
            cc[termios.VMIN], cc[termios.VTIME] = 0, 0
            speed = getattr(termios, f"B{c.baud}")
            termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
        except (termios.error, OSError) as e:
            os.close(fd)
            raise SerialError(f"Portul {c.port}: setările nu s-au putut aplica ({e})") from e
        self.fd = fd

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def write(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 2.0
        while view:
            _, scriere, _ = select.select([], [self.fd], [], max(0.0, deadline - time.monotonic()))
            if not scriere:
                raise SerialError(f"Portul {self.cfg.port}: scrierea nu s-a terminat în 2 secunde")
            try:
                n = os.write(self.fd, view)
            except BlockingIOError:
                continue
            except OSError as e:
                raise SerialError(_mesaj(self.cfg.port, e)) from e
            view = view[n:]

    def read(self, timeout):
        try:
            gata, _, _ = select.select([self.fd], [], [], timeout)
            if not gata:
                return b""
            date = os.read(self.fd, 4096)
            if not date:       # „gata de citit", dar nimic de citit = echipamentul a dispărut; altfel apelantul ar învârti bucla până la timeout
                raise SerialError(f"Portul {self.cfg.port}: conexiunea cu echipamentul s-a închis")
            return date
        except BlockingIOError:
            return b""
        except OSError as e:
            raise SerialError(_mesaj(self.cfg.port, e)) from e

    def purge(self):
        import termios
        termios.tcflush(self.fd, termios.TCIFLUSH)


# --------------------------------------------------------------------------------------------------------- Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID = wintypes.HANDLE(-1).value
    _MAXDWORD = 0xFFFFFFFF

    class _DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD),
            ("fBinary", wintypes.DWORD, 1), ("fParity", wintypes.DWORD, 1), ("fOutxCtsFlow", wintypes.DWORD, 1),
            ("fOutxDsrFlow", wintypes.DWORD, 1), ("fDtrControl", wintypes.DWORD, 2), ("fDsrSensitivity", wintypes.DWORD, 1),
            ("fTXContinueOnXoff", wintypes.DWORD, 1), ("fOutX", wintypes.DWORD, 1), ("fInX", wintypes.DWORD, 1),
            ("fErrorChar", wintypes.DWORD, 1), ("fNull", wintypes.DWORD, 1), ("fRtsControl", wintypes.DWORD, 2),
            ("fAbortOnError", wintypes.DWORD, 1), ("fDummy2", wintypes.DWORD, 17),
            ("wReserved", wintypes.WORD), ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
            ("ByteSize", wintypes.BYTE), ("Parity", wintypes.BYTE), ("StopBits", wintypes.BYTE),
            ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", wintypes.WORD),
        ]

    class _TIMEOUTS(ctypes.Structure):
        _fields_ = [("ReadIntervalTimeout", wintypes.DWORD), ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                    ("ReadTotalTimeoutConstant", wintypes.DWORD), ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                    ("WriteTotalTimeoutConstant", wintypes.DWORD)]

    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE]
    for _nume in ("GetCommState", "SetCommState", "SetCommTimeouts", "PurgeComm", "CloseHandle"):
        getattr(_k32, _nume).restype = wintypes.BOOL
    _k32.GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
    _k32.SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
    _k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(_TIMEOUTS)]
    _k32.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]

    class _WinPort:
        def __init__(self, cfg):
            self.cfg, self.h = cfg, None

        def _eroare(self, ce):
            return SerialError(_mesaj(self.cfg.port, ctypes.get_last_error()) if ce is None else f"Portul {self.cfg.port}: {ce}")

        def open(self):
            c = self.cfg
            h = _k32.CreateFileW(device_path(c.port), 0xC0000000, 0, None, 3, 0, None)   # GENERIC_READ|GENERIC_WRITE, OPEN_EXISTING
            if h is None or h == _INVALID:
                raise self._eroare(None)
            dcb = _DCB()
            dcb.DCBlength = ctypes.sizeof(_DCB)
            if not _k32.GetCommState(h, ctypes.byref(dcb)):
                _k32.CloseHandle(h)
                raise self._eroare("starea portului nu s-a putut citi")
            dcb.BaudRate, dcb.ByteSize = c.baud, c.bytesize
            dcb.Parity = {"N": 0, "O": 1, "E": 2}[c.parity]
            dcb.StopBits = 0 if c.stopbits == 1 else 2
            dcb.fBinary, dcb.fParity = 1, 0 if c.parity == "N" else 1
            dcb.fOutxCtsFlow = dcb.fOutxDsrFlow = dcb.fDsrSensitivity = dcb.fOutX = dcb.fInX = 0
            dcb.fErrorChar = dcb.fNull = dcb.fAbortOnError = 0
            dcb.fDtrControl = dcb.fRtsControl = 1   # DTR și RTS ridicate: modulele USB-serial o cer pentru a vorbi
            if not _k32.SetCommState(h, ctypes.byref(dcb)):
                _k32.CloseHandle(h)
                raise self._eroare("setările portului (viteză/paritate) nu au fost acceptate")
            self.h = h

        def close(self):
            h, self.h = self.h, None
            if h is not None:
                _k32.CloseHandle(h)

        def write(self, data):
            t = _TIMEOUTS(_MAXDWORD, 0, 0, 0, 2000)
            _k32.SetCommTimeouts(self.h, ctypes.byref(t))
            view = bytes(data)
            while view:
                scrise = wintypes.DWORD(0)
                if not _k32.WriteFile(self.h, view, len(view), ctypes.byref(scrise), None) or scrise.value == 0:
                    raise self._eroare(None if not scrise.value else "scrierea nu s-a terminat")
                view = view[scrise.value:]

        def read(self, timeout):
            ms = int(timeout * 1000)
            # (MAXDWORD, MAXDWORD, ms): întoarce imediat ce sunt octeți în buffer; altfel așteaptă primul octet cel mult `ms`
            t = _TIMEOUTS(_MAXDWORD, _MAXDWORD, ms, 0, 2000) if ms > 0 else _TIMEOUTS(_MAXDWORD, 0, 0, 0, 2000)
            _k32.SetCommTimeouts(self.h, ctypes.byref(t))
            buf = ctypes.create_string_buffer(4096)
            citit = wintypes.DWORD(0)
            if not _k32.ReadFile(self.h, buf, 4096, ctypes.byref(citit), None):
                raise self._eroare(None)
            return buf.raw[:citit.value]

        def purge(self):
            _k32.PurgeComm(self.h, 0x0002 | 0x0008)   # PURGE_RXABORT | PURGE_RXCLEAR
