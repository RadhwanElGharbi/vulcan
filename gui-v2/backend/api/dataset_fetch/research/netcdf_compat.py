"""Narrow workaround for upstream netCDF4 issue 1354, fixed by PR 1471.

The Cython ndarray declaration intentionally contains no fields (16-byte
PyObject). Its size warning does not compare NumPy's actual C-API ABI. The
upstream fix uses check_size ignore. Keep all other runtime warnings enabled.
https://github.com/Unidata/netcdf4-python/pull/1471
"""
import warnings


def load_netcdf():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"^numpy\.ndarray size changed, may indicate binary incompatibility\. Expected 16 from C header, got 96 from PyObject$", category=RuntimeWarning)
        import netCDF4
    return netCDF4
