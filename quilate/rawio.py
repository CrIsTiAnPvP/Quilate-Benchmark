"""E/S de disco saltándose la caché del sistema operativo.

Sin esto, un test de disco no mide el disco. El fichero recién escrito se queda
en la caché de páginas, y la lectura posterior sale de la RAM: en un equipo con
16 GB, un fichero de prueba de 512 MB nunca llega a tocar el hardware. Los
síntomas son inconfundibles —lectura varias veces más rápida que la escritura
del mismo fichero, latencias de microsegundos, cientos de miles de IOPS— y
convierten la nota de disco en una medida de la memoria.

En Windows se abre el fichero con FILE_FLAG_NO_BUFFERING, que obliga a que
desplazamientos, tamaños y direcciones de buffer sean múltiplos del sector.
En Linux basta con pedir al núcleo que descarte la caché del fichero
(posix_fadvise DONTNEED) entre la escritura y la lectura. En el resto de
sistemas se cae a E/S normal, y quien llame debe asumir que la medida no es
fiable: `DiskIO.unbuffered` lo indica.
"""

from __future__ import annotations

import ctypes
import os

from .const import IS_WINDOWS

# Múltiplo válido tanto para sectores de 512 B como de 4 KiB.
ALIGN = 4096

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_CREATE_ALWAYS = 2
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_WRITE_THROUGH = 0x80000000
_FILE_FLAG_NO_BUFFERING = 0x20000000
_FILE_BEGIN = 0


def _aligned_buffer(size: int) -> tuple:
    """Buffer cuya dirección es múltiplo de ALIGN, requisito de la E/S directa.

    Se reserva de más y se apunta al primer offset alineado; hay que conservar
    el objeto original vivo mientras se use el puntero.
    """
    backing = (ctypes.c_char * (size + ALIGN))()
    offset = (-ctypes.addressof(backing)) % ALIGN
    return ctypes.byref(backing, offset), backing


class DiskIO:
    """Fichero de prueba. `unbuffered` dice si la medida esquiva la caché."""

    def __init__(self, path: str, write: bool, block: int):
        self.path = path
        self.block = block
        self.unbuffered = False
        self._handle = None
        self._fd = None
        self._ptr, self._backing = _aligned_buffer(block)
        self._data = b"\0" * block

        if IS_WINDOWS:
            self._open_windows(write)
        if self._handle is None:
            self._open_posix(write)

    # ------------------------------------------------------------- apertura --
    def _open_windows(self, write: bool) -> None:
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.restype = ctypes.c_void_p
            flags = _FILE_FLAG_NO_BUFFERING | (_FILE_FLAG_WRITE_THROUGH if write else 0)
            handle = k32.CreateFileW(
                ctypes.c_wchar_p(self.path),
                ctypes.c_uint32(_GENERIC_WRITE if write else _GENERIC_READ),
                ctypes.c_uint32(0 if write else _FILE_SHARE_READ),
                None,
                ctypes.c_uint32(_CREATE_ALWAYS if write else _OPEN_EXISTING),
                ctypes.c_uint32(_FILE_ATTRIBUTE_NORMAL | flags),
                None)
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                return
            self._k32 = k32
            self._handle = handle
            self.unbuffered = True
        except (OSError, AttributeError, ValueError):
            self._handle = None

    def _open_posix(self, write: bool) -> None:
        flags = os.O_RDONLY
        if write:
            flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
        self._fd = os.open(self.path, flags | getattr(os, "O_BINARY", 0))

    # ------------------------------------------------------------------ E/S --
    def write(self, n: int) -> int:
        if self._handle is not None:
            done = ctypes.c_uint32(0)
            if not self._k32.WriteFile(ctypes.c_void_p(self._handle), self._ptr,
                                       ctypes.c_uint32(n), ctypes.byref(done), None):
                raise OSError(ctypes.get_last_error(), "WriteFile")
            return done.value
        return os.write(self._fd, self._data[:n])

    def read(self, n: int) -> int:
        if self._handle is not None:
            done = ctypes.c_uint32(0)
            if not self._k32.ReadFile(ctypes.c_void_p(self._handle), self._ptr,
                                      ctypes.c_uint32(n), ctypes.byref(done), None):
                raise OSError(ctypes.get_last_error(), "ReadFile")
            return done.value
        return len(os.read(self._fd, n))

    def seek(self, offset: int) -> None:
        if self._handle is not None:
            if not self._k32.SetFilePointerEx(ctypes.c_void_p(self._handle),
                                              ctypes.c_longlong(offset), None, _FILE_BEGIN):
                raise OSError(ctypes.get_last_error(), "SetFilePointerEx")
            return
        os.lseek(self._fd, offset, os.SEEK_SET)

    def sync(self) -> None:
        """Sin FILE_FLAG_WRITE_THROUGH hay que forzar el vaciado a mano; con él
        el dato ya está en el disco cuando WriteFile retorna."""
        if self._fd is not None:
            os.fsync(self._fd)

    def drop_cache(self) -> bool:
        """Descarta la caché de páginas del fichero. Es la alternativa a la E/S
        directa en Linux; en Windows no hay equivalente público, pero allí ya se
        abre sin buffer."""
        fadvise = getattr(os, "posix_fadvise", None)
        if fadvise is None or self._fd is None:
            return False
        try:
            os.fsync(self._fd)
            fadvise(self._fd, 0, 0, os.POSIX_FADV_DONTNEED)
            return True
        except OSError:
            return False

    def fill_random(self) -> None:
        """Datos incompresibles, para que la compresión transparente de algunos
        SSD no infle la velocidad de escritura."""
        self._data = os.urandom(self.block)
        ctypes.memmove(self._ptr, self._data, self.block)

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None
        elif self._fd is not None:
            os.close(self._fd)
            self._fd = None
