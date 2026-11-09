# refrata_native (Rust)

Módulo nativo do Refrata, em Rust (PyO3 + maturin). Hoje traz `FramePool`: os callbacks de cada quadro do
libVLC (lock / unlock / display) sem o GIL do Python. É experimental e fica desligado por padrão; liga com
`REFRATA_NATIVE=1`. As medições e o plano estão em
[docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md](../docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md).

## Compilar

No Windows não precisa do Visual Studio: o toolchain GNU do Rust traz o próprio linker.

```
rustup toolchain install stable-x86_64-pc-windows-gnu --profile minimal
cd native
rustup override set stable-x86_64-pc-windows-gnu
pip install maturin
maturin build --release -o ../build/wheels
pip install ../build/wheels/refrata_native-*.whl
```

No Linux e no macOS: `pip install maturin && maturin build --release` com o toolchain padrão.

Sem o módulo, o app usa o caminho em Python: nada depende dele.
