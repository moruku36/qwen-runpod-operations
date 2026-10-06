# October 5 build-toolchain correction (CPU validation candidate)

## Failure and scope

The root-import corrected revision `76505a3` reached the pinned llama.cpp checkout,
then failed because `cmake` was not on the actual build subprocess PATH. The
previous code checked `nvcc`, but neither provisioned nor validated CMake/Ninja.
A successful Python runtime lock install did not establish native build readiness.
No GPU acceptance result was produced. This correction does not authorize a paid
launch, change the evaluation profile, shorten safety deadlines, or bypass the
independently downloaded selftest receipt.

## Order and installation

After the existing selftest/off-Pod receipt gate, the `net` stage now:

1. Audits CPython 3.11 on 64-bit Linux x86_64, git, gcc, g++, nvcc and nvidia-smi.
   Missing system prerequisites are collected before any package download or
   source clone. C/C++ and CUDA compile/link probes never execute a GPU kernel.
2. Creates the existing isolated workspace `venv`, with the existing host-pip
   fallback when ensurepip is unavailable.
3. Installs only the two reviewed binary wheels from official PyPI using the
   separate `requirements-build-tools.lock.txt`, exact hashes, `--no-deps`,
   `--only-binary=:all:` and an explicit official index. The application runtime
   hash lock is unchanged. System packages, drivers and global Python are not
   modified.
4. Checks the package versions and real `venv/bin` executables, then records
   `runs/toolchain.json`. Normal network checks, source checkout and the larger
   runtime dependency installation follow only when this passes.

Direct source, venv and build entrypoints require/revalidate the successful
receipt. The receipt is bound to the workspace root and build-tool lock hash.
A stale or failed receipt is not accepted. This does not turn a receipt into a
security boundary against an actor who can edit the source or local files.

Executing a venv Python by full path does not activate its PATH. `activation_env`
now puts the venv bin directory first, sets VIRTUAL_ENV and PYTHONNOUSERSITE, and
removes PYTHONHOME. The CLI passes this environment through `os.execve`, and the
stage/build subprocesses use it. CMake is also given the audited gcc, g++, nvcc,
CUDA host compiler and Ninja absolute paths, rather than relying on unrelated
CC/CXX defaults. Package version checks alone are insufficient.

## Fixed official distributions

- CMake `3.31.6`, wheel `cmake-3.31.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
  - SHA256 `1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec`
  - https://pypi.org/project/cmake/3.31.6/#files
- Ninja `1.11.1.3`, wheel `ninja-1.11.1.3-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.whl`
  - SHA256 `a27e78ca71316c8654965ee94b286a98c83877bfebe2607db96897bbfe458af0`
  - https://pypi.org/project/ninja/1.11.1.3/#files
  - Actual executable version is `1.11.1.git.kitware.jobserver-1`; the wheel
    distribution revision is intentionally different.

For a separately prepared private offline wheelhouse, download with the same
`--require-hashes --only-binary=:all: --no-deps` lock and install with `--no-index
--find-links <wheelhouse>`. The GPU driver flow itself uses the reviewed official
index. This candidate does not silently switch indexes or fall back to an sdist.

## Native prerequisites and remaining limits

The pinned llama.cpp needs C11/C++17/assembler support, Threads, CUDA development
headers and linker inputs for cudart, cuBLAS and the CUDA driver. The early CUDA
probe compiles for numeric SM80 (the approved A100 scope) and links without
executing the resulting program. CUDA driver stub directories may be used only
for linking, never added to runtime LD_LIBRARY_PATH. A working nvidia-smi is not
proof that nvcc or the development libraries are installed. Missing system tools
require a properly prepared official CUDA development image; there is no automatic
apt install or driver replacement.

libcurl-dev is not required by the pinned source. OpenSSL >=3 support is optional
for HTTPS. The trial serves a local pinned model and does not require this optional
HTTPS capability. This candidate does not claim Blackwell/family-architecture
support or compatibility for an arbitrary future llama.cpp revision.

`BUILD_SHARED_LIBS=OFF` does not make the server completely static. Existing cache
keys omit some image/toolkit/compiler details. Continue using a fresh run root;
this correction makes no cache portability or time-saving claim.

## CPU evidence versus GPU evidence

Real CPU validation includes exact hash-locked wheel installation, a CPython3.11
venv subprocess resolving both tools from its own bin directory, and an actual
CMake/Ninja C++17/Threads configure/build/run. Regression tests cover early failure,
aggregate missing tools, lock/receipt validation and subprocess activation.

The cloud CPU environment has no CUDA toolkit or GPU. Its real preflight correctly
fails on missing nvcc/nvidia-smi without downloading dependencies. CUDA smoke
success, llama.cpp CUDA compilation, model loading, cold/warm inference and GPU
acceptance remain unverified. CPU mocks are not substitutes for those results.
