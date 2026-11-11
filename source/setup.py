"""Build configuration for the C extension; everything else lives in pyproject.toml."""

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "jtty_snr_study._rtty",
            sources=["src/jtty_snr_study/_rtty.c"],
            extra_compile_args=["-O2", "-std=c99", "-Wall", "-Wextra"],
            libraries=["m"],
        ),
        Extension(
            "jtty_snr_study._psk31",
            sources=["src/jtty_snr_study/_psk31.c"],
            extra_compile_args=["-O2", "-std=c99", "-Wall", "-Wextra"],
            libraries=["m"],
        ),
    ]
)
