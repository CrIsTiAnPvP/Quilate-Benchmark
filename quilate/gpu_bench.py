"""Medida real de la GPU a traves de OpenCL, sin dependencias externas.

Quilate auditaba el driver de la grafica, leia su temperatura y no la ponia a
trabajar nunca: en un equipo con una tarjeta dedicada, la pieza mas cara del PC
no aparecia en la puntuacion. Para alguien que quiere saber si le va a ir bien
un juego, ese era el dato que faltaba.

Aqui se habla con `OpenCL.dll` (Windows) o `libOpenCL.so.1` (Linux) por ctypes.
No hace falta instalar nada: la biblioteca la pone el propio driver de la GPU,
sea NVIDIA, AMD o Intel. Si no esta, o si no hay ningun dispositivo, no se
inventa una cifra: se dice que no se ha podido medir y por que.

Se miden tres cosas distintas a proposito, porque un cuello de botella en una
no se parece en nada al de las otras:

  - Computo FP32: cuanta aritmetica hace el chip.
  - Ancho de banda de VRAM: a que velocidad alimenta a ese chip su memoria.
  - Transferencia PCIe: cuanto cuesta meter y sacar datos del sistema.
"""

from __future__ import annotations

import ctypes as C
import time
from typing import Any

from .const import IS_LINUX, IS_MAC, IS_WINDOWS

# --- Constantes de la especificacion OpenCL ----------------------------------
CL_SUCCESS = 0
CL_DEVICE_TYPE_GPU = 1 << 2
CL_PLATFORM_NAME = 0x0902
CL_DEVICE_NAME = 0x102B
CL_DEVICE_VENDOR = 0x102C
CL_DEVICE_VERSION = 0x102F
CL_DRIVER_VERSION = 0x102D      # 0x102E es CL_DEVICE_PROFILE, no la version
CL_DEVICE_MAX_COMPUTE_UNITS = 0x1002
CL_DEVICE_MAX_CLOCK_FREQUENCY = 0x100C
CL_DEVICE_GLOBAL_MEM_SIZE = 0x101F
CL_DEVICE_MAX_WORK_GROUP_SIZE = 0x1004
CL_MEM_READ_WRITE = 1 << 0
CL_PROGRAM_BUILD_LOG = 0x1183

# Dos FMA por vuelta del bucle, y cada FMA cuenta como dos operaciones.
_FLOPS_POR_ITERACION = 4

# El kernel encadena las operaciones a proposito: cada FMA depende del
# resultado de la anterior, asi que el compilador no puede eliminarlas ni
# vectorizarlas gratis. Lo que sale es trabajo que la GPU ha hecho de verdad.
_FUENTE = b"""
__kernel void quilate_fma(__global float* salida, const int vueltas) {
    int i = get_global_id(0);
    float a = (float)i * 1e-6f;
    float b = 1.0000001f;
    float c = 0.9999999f;
    for (int k = 0; k < vueltas; k++) {
        a = fma(a, b, c);
        a = fma(a, c, b);
    }
    salida[i] = a;
}

__kernel void quilate_copia(__global const float4* origen, __global float4* destino) {
    int i = get_global_id(0);
    destino[i] = origen[i];
}
"""


class GPUNoDisponible(Exception):
    """No hay forma de medir la GPU en este equipo, y se explica por que."""


def _nombre_biblioteca() -> tuple[str, ...]:
    if IS_WINDOWS:
        return ("OpenCL.dll",)
    if IS_MAC:
        return ("/System/Library/Frameworks/OpenCL.framework/OpenCL",)
    if IS_LINUX:
        return ("libOpenCL.so.1", "libOpenCL.so")
    return ()


def _cargar() -> C.CDLL:
    ultimo = ""
    for nombre in _nombre_biblioteca():
        try:
            return C.CDLL(nombre)
        except OSError as exc:
            ultimo = str(exc)
    raise GPUNoDisponible(
        "no se ha encontrado OpenCL en este equipo. Lo instala el propio driver "
        "de la grafica, asi que suele significar que falta el driver o que solo "
        "hay un adaptador basico de Windows"
        + (f" ({ultimo[:60]})" if ultimo else ""))


def _firmar(lib: C.CDLL) -> None:
    """Sin declarar los tipos, un puntero de 64 bits se trunca a 32 y la
    llamada devuelve basura o revienta el proceso."""
    p, u, s, i = C.c_void_p, C.c_uint, C.c_size_t, C.c_int
    lib.clGetPlatformIDs.argtypes = [u, C.POINTER(p), C.POINTER(u)]
    lib.clGetPlatformInfo.argtypes = [p, u, s, p, C.POINTER(s)]
    lib.clGetDeviceIDs.argtypes = [p, C.c_ulonglong, u, C.POINTER(p), C.POINTER(u)]
    lib.clGetDeviceInfo.argtypes = [p, u, s, p, C.POINTER(s)]
    lib.clCreateContext.restype = p
    lib.clCreateContext.argtypes = [p, u, C.POINTER(p), p, p, C.POINTER(i)]
    lib.clCreateCommandQueue.restype = p
    lib.clCreateCommandQueue.argtypes = [p, p, C.c_ulonglong, C.POINTER(i)]
    lib.clCreateProgramWithSource.restype = p
    lib.clCreateProgramWithSource.argtypes = [p, u, C.POINTER(C.c_char_p),
                                              C.POINTER(s), C.POINTER(i)]
    lib.clBuildProgram.argtypes = [p, u, C.POINTER(p), C.c_char_p, p, p]
    lib.clGetProgramBuildInfo.argtypes = [p, p, u, s, p, C.POINTER(s)]
    lib.clCreateKernel.restype = p
    lib.clCreateKernel.argtypes = [p, C.c_char_p, C.POINTER(i)]
    lib.clCreateBuffer.restype = p
    lib.clCreateBuffer.argtypes = [p, C.c_ulonglong, s, p, C.POINTER(i)]
    lib.clSetKernelArg.argtypes = [p, u, s, p]
    lib.clEnqueueNDRangeKernel.argtypes = [p, p, u, C.POINTER(s), C.POINTER(s),
                                           C.POINTER(s), u, p, p]
    lib.clEnqueueWriteBuffer.argtypes = [p, p, u, s, s, p, u, p, p]
    lib.clEnqueueReadBuffer.argtypes = lib.clEnqueueWriteBuffer.argtypes
    lib.clFinish.argtypes = [p]
    for liberar in ("clReleaseMemObject", "clReleaseKernel", "clReleaseProgram",
                    "clReleaseCommandQueue", "clReleaseContext"):
        getattr(lib, liberar).argtypes = [p]


def _texto(lib: C.CDLL, fn, obj, param: int) -> str:
    n = C.c_size_t()
    if fn(obj, param, 0, None, C.byref(n)) != CL_SUCCESS or not n.value:
        return ""
    buf = C.create_string_buffer(n.value)
    fn(obj, param, n.value, C.cast(buf, C.c_void_p), None)
    return buf.raw.split(b"\0")[0].decode("utf-8", "replace").strip()


def _numero(lib: C.CDLL, fn, obj, param: int, tipo=C.c_uint) -> int:
    valor = tipo()
    if fn(obj, param, C.sizeof(valor), C.byref(valor), None) != CL_SUCCESS:
        return 0
    return int(valor.value)


def _dispositivos(lib: C.CDLL) -> list[dict]:
    """Todas las GPU que exponga cualquier plataforma instalada."""
    n = C.c_uint()
    if lib.clGetPlatformIDs(0, None, C.byref(n)) != CL_SUCCESS or not n.value:
        raise GPUNoDisponible("OpenCL esta instalado pero no declara ninguna plataforma")
    plataformas = (C.c_void_p * n.value)()
    lib.clGetPlatformIDs(n.value, plataformas, None)

    encontrados = []
    for plat in plataformas:
        nd = C.c_uint()
        if lib.clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, 0, None, C.byref(nd)) != CL_SUCCESS:
            continue
        if not nd.value:
            continue
        devs = (C.c_void_p * nd.value)()
        lib.clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, nd.value, devs, None)
        for dev in devs:
            encontrados.append({
                "id": dev,
                "platform": _texto(lib, lib.clGetPlatformInfo, plat, CL_PLATFORM_NAME),
                "name": _texto(lib, lib.clGetDeviceInfo, dev, CL_DEVICE_NAME),
                "vendor": _texto(lib, lib.clGetDeviceInfo, dev, CL_DEVICE_VENDOR),
                "opencl": _texto(lib, lib.clGetDeviceInfo, dev, CL_DEVICE_VERSION),
                "driver": _texto(lib, lib.clGetDeviceInfo, dev, CL_DRIVER_VERSION),
                "compute_units": _numero(lib, lib.clGetDeviceInfo, dev,
                                         CL_DEVICE_MAX_COMPUTE_UNITS),
                "clock_mhz": _numero(lib, lib.clGetDeviceInfo, dev,
                                     CL_DEVICE_MAX_CLOCK_FREQUENCY),
                "vram": _numero(lib, lib.clGetDeviceInfo, dev,
                                CL_DEVICE_GLOBAL_MEM_SIZE, C.c_ulonglong),
                "max_work_group": _numero(lib, lib.clGetDeviceInfo, dev,
                                          CL_DEVICE_MAX_WORK_GROUP_SIZE, C.c_size_t),
            })
    if not encontrados:
        raise GPUNoDisponible("OpenCL no encuentra ninguna GPU. Con controlador basico "
                              "de Windows o en escritorio remoto es lo normal")
    return encontrados


def elegir_dispositivo(dispositivos: list[dict]) -> dict:
    """La GPU con mas capacidad de calculo.

    El mismo criterio de siempre: en un equipo con dedicada e integrada, las dos
    aparecen, y medir la que no mueve nada seria repetir el error del driver.
    Unidades de computo por frecuencia es la mejor aproximacion disponible sin
    una tabla de modelos que envejeceria sola.
    """
    return max(dispositivos, key=lambda d: (d["compute_units"] * max(1, d["clock_mhz"]),
                                            d["vram"]))


class _Sesion:
    """Contexto, cola y kernels, con todo liberado al salir."""

    def __init__(self, lib: C.CDLL, dispositivo: dict):
        self.lib = lib
        self.dev = dispositivo["id"]
        self.buffers: list[C.c_void_p] = []
        err = C.c_int()
        uno = (C.c_void_p * 1)(self.dev)

        self.ctx = lib.clCreateContext(None, 1, uno, None, None, C.byref(err))
        if err.value != CL_SUCCESS or not self.ctx:
            raise GPUNoDisponible(f"no se ha podido crear el contexto OpenCL ({err.value})")
        self.queue = lib.clCreateCommandQueue(self.ctx, self.dev, 0, C.byref(err))
        if err.value != CL_SUCCESS or not self.queue:
            raise GPUNoDisponible(f"no se ha podido crear la cola de comandos ({err.value})")

        fuentes = (C.c_char_p * 1)(_FUENTE)
        self.prog = lib.clCreateProgramWithSource(self.ctx, 1, fuentes, None, C.byref(err))
        if err.value != CL_SUCCESS:
            raise GPUNoDisponible(f"no se ha podido cargar el programa ({err.value})")
        if lib.clBuildProgram(self.prog, 1, uno, None, None, None) != CL_SUCCESS:
            log = _texto(lib, lambda o, p, s, b, n: lib.clGetProgramBuildInfo(
                o, self.dev, p, s, b, n), self.prog, CL_PROGRAM_BUILD_LOG)
            raise GPUNoDisponible(f"el compilador del driver ha rechazado el kernel: "
                                  f"{log[:120] or 'sin detalle'}")
        self.k_fma = lib.clCreateKernel(self.prog, b"quilate_fma", C.byref(err))
        self.k_copia = lib.clCreateKernel(self.prog, b"quilate_copia", C.byref(err))
        if err.value != CL_SUCCESS:
            raise GPUNoDisponible(f"no se han podido crear los kernels ({err.value})")

    def buffer(self, nbytes: int) -> C.c_void_p:
        err = C.c_int()
        buf = self.lib.clCreateBuffer(self.ctx, CL_MEM_READ_WRITE, nbytes, None, C.byref(err))
        if err.value != CL_SUCCESS or not buf:
            raise GPUNoDisponible(f"la GPU no ha podido reservar "
                                  f"{nbytes / 1024**2:.0f} MiB ({err.value})")
        self.buffers.append(C.c_void_p(buf))
        return C.c_void_p(buf)

    def lanzar(self, kernel, hilos: int) -> None:
        globales = (C.c_size_t * 1)(hilos)
        rc = self.lib.clEnqueueNDRangeKernel(self.queue, kernel, 1, None, globales,
                                             None, 0, None, None)
        if rc != CL_SUCCESS:
            raise GPUNoDisponible(f"la GPU ha rechazado la ejecucion del kernel ({rc})")

    def esperar(self) -> None:
        # Las llamadas son asincronas: sin esto se cronometraria el encolado.
        self.lib.clFinish(self.queue)

    def cerrar(self) -> None:
        for buf in self.buffers:
            self.lib.clReleaseMemObject(buf)
        for objeto, liberar in ((self.k_fma, "clReleaseKernel"),
                                (self.k_copia, "clReleaseKernel"),
                                (self.prog, "clReleaseProgram"),
                                (self.queue, "clReleaseCommandQueue"),
                                (self.ctx, "clReleaseContext")):
            try:
                getattr(self.lib, liberar)(C.c_void_p(objeto))
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()


def _arg_puntero(lib: C.CDLL, kernel, indice: int, buf: C.c_void_p) -> None:
    lib.clSetKernelArg(kernel, indice, C.sizeof(C.c_void_p), C.byref(buf))


def _arg_entero(lib: C.CDLL, kernel, indice: int, valor: int) -> None:
    lib.clSetKernelArg(kernel, indice, 4, C.byref(C.c_int(valor)))


def medir_gpu(rapido: bool = False) -> dict[str, Any]:
    """Computo FP32, ancho de banda de VRAM y transferencia PCIe.

    Devuelve tambien las tres listas de muestras para que el margen se calcule
    igual que en el resto del benchmark: una cifra de GPU sin dispersion es tan
    poco comparable como una de disco.
    """
    lib = _cargar()
    _firmar(lib)
    dispositivos = _dispositivos(lib)
    dev = elegir_dispositivo(dispositivos)

    repeticiones = 2 if rapido else 3
    resultado: dict[str, Any] = {
        "device": {k: v for k, v in dev.items() if k != "id"},
        "devices": [{k: v for k, v in d.items() if k != "id"} for d in dispositivos],
    }

    with _Sesion(lib, dev) as ses:
        # --- Computo FP32 ---
        # El numero de vueltas se calibra en vez de fijarse: con una cifra fija,
        # una tarjeta rapida despacha el kernel en 50 ms —demasiado corto, la
        # dispersion se disparaba al 18%— y una integrada tardaria una eternidad.
        hilos = 1024 * 1024
        objetivo = 0.12 if rapido else 0.30
        salida = ses.buffer(hilos * 4)
        _arg_puntero(lib, ses.k_fma, 0, salida)
        vueltas = 64
        _arg_entero(lib, ses.k_fma, 1, vueltas)
        ses.lanzar(ses.k_fma, hilos)      # calentamiento: la primera compila y sube
        ses.esperar()
        t0 = time.perf_counter()
        ses.lanzar(ses.k_fma, hilos)
        ses.esperar()
        sonda = time.perf_counter() - t0
        if sonda > 0:
            # El tope existe por el vigilante de Windows: un kernel que pase de
            # ~2 s reinicia el driver. Con objetivo de 0,3 s hay margen de sobra
            # aunque la sonda se equivoque por un factor de cuatro.
            vueltas = max(64, min(262144, int(vueltas * objetivo / sonda)))
        _arg_entero(lib, ses.k_fma, 1, vueltas)
        resultado["compute_iters"] = vueltas
        ses.lanzar(ses.k_fma, hilos)
        ses.esperar()
        gflops = []
        for _ in range(repeticiones):
            t0 = time.perf_counter()
            ses.lanzar(ses.k_fma, hilos)
            ses.esperar()
            dt = time.perf_counter() - t0
            if dt > 0:
                gflops.append(hilos * vueltas * _FLOPS_POR_ITERACION / dt / 1e9)
        resultado["gflops"] = max(gflops) if gflops else 0.0
        resultado["gflops_samples"] = gflops

        # --- Ancho de banda de VRAM ---
        # Se limita a un octavo de la memoria de la tarjeta: en una integrada
        # que declara 8 GB "compartidos" reservar 128 MiB de golpe puede fallar.
        tope = max(16 * 1024 * 1024, min(128 * 1024 * 1024, (dev["vram"] or 0) // 8))
        nbytes = (tope // 16) * 16
        origen, destino = ses.buffer(nbytes), ses.buffer(nbytes)
        _arg_puntero(lib, ses.k_copia, 0, origen)
        _arg_puntero(lib, ses.k_copia, 1, destino)
        ses.lanzar(ses.k_copia, nbytes // 16)
        ses.esperar()
        vram = []
        for _ in range(repeticiones):
            t0 = time.perf_counter()
            ses.lanzar(ses.k_copia, nbytes // 16)
            ses.esperar()
            dt = time.perf_counter() - t0
            if dt > 0:
                # Se cuentan las dos caras: una lectura y una escritura.
                vram.append(nbytes * 2 / dt / 1e9)
        resultado["vram_gbs"] = max(vram) if vram else 0.0
        resultado["vram_samples"] = vram

        # --- Transferencia por PCIe ---
        host = (C.c_char * nbytes)()
        puntero = C.cast(host, C.c_void_p)
        lib.clEnqueueWriteBuffer(ses.queue, origen, 1, 0, nbytes, puntero, 0, None, None)
        ses.esperar()
        pcie = []
        for _ in range(repeticiones):
            t0 = time.perf_counter()
            lib.clEnqueueWriteBuffer(ses.queue, origen, 1, 0, nbytes, puntero, 0, None, None)
            lib.clEnqueueReadBuffer(ses.queue, origen, 1, 0, nbytes, puntero, 0, None, None)
            ses.esperar()
            dt = time.perf_counter() - t0
            if dt > 0:
                pcie.append(nbytes * 2 / dt / 1e9)
        resultado["pcie_gbs"] = max(pcie) if pcie else 0.0
        resultado["pcie_samples"] = pcie

    return resultado
