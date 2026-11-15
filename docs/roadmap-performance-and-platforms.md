# Roteiro: desempenho, Rust e outros sistemas

Auditoria de novembro de 2026 (versões 1.4.3 e 1.4.4). Onde o Refrata gasta tempo, o que foi corrigido,
o que o Rust ganha de verdade (medido) e o caminho para Linux e macOS.

## 1. Onde o tempo vai

| Etapa | Quem faz | Custo |
|---|---|---|
| Decodificar vídeo | libVLC (C), na GPU desde a 1.4.4 | o maior, já nativo |
| Levar o quadro até a tela | libVLC escreve direto na memória de um `QVideoFrame` (I420 ou NV12); o Qt converte para RGB na GPU | 1 cópia |
| Avisar que o quadro chegou | 3 chamadas Python por quadro na thread do vídeo + 1 na da interface | pequeno, mas passa pelo GIL |
| Interface | Qt Quick (C++), desenho na GPU | nativo |
| Abrir o app | ~0,5 s importando Python, ~1,6 s montando o QML | o QML é C++ |
| Biblioteca | `os.scandir` (C), miniaturas pelo libVLC | só ao atualizar |

## 2. O que foi corrigido

Desempenho
- **Decodificação por hardware (1.4.4).** Ela nunca funcionou: o libVLC 3 desliga a GPU quando o app recebe
  os quadros por callbacks (força `avcodec-hw=none` no player). Uma opção por mídia (`:avcodec-hw=any`) vence
  isso. 4K60 H.264: **1,14 → 0,36 núcleo (−68%)**, 60 fps, sem travadas.
  Limite: nesse caminho o libVLC 3 não desenha legendas sobre quadros decodificados na GPU. Um item com legenda
  ligada (escolhida, adicionada, ou ligada pelo próprio arquivo) é reaberto em software no mesmo ponto
  (`Engine._to_software`); a bateria robust confere a legenda na tela comparando com o mesmo trecho sem ela.
- **Sem conversão à toa (1.4.4).** O decodificador por software entrega I420, e pedíamos NV12, então o libVLC
  convertia cada quadro na CPU (~750 MB/s em 4K60). Agora aceitamos o formato dele: **−15% de CPU** sem GPU.
- **GIL (1.4.4).** Intervalo de troca do Python em 1 ms (era 5): com outra thread Python ocupada, 4K60 passa de
  **~3 para ~20 quadros por segundo** entregues; sem carga, nada muda.
- **Parado (1.4.3).** O app consultava o libVLC 8×/s para sempre; agora só tocando. CPU parado: 11,5 → 1,6 ms/s.
- **Teclado (1.4.3).** O gancho da prévia com Espaço só existe com o Explorer ou a área de trabalho na frente.
- **Abertura (1.4.3).** Tela de abertura em ~0,2 s após o clique.

Segurança (1.4.3)
- Playlists não podem apontar para pastas de rede de outro servidor (vazava o hash NTLM da senha).
- Atualizador só por HTTPS do GitHub; legendas só HTTPS e até 8 MB; mensagens entre instâncias até 4 MB.

Estabilidade (1.4.4)
- Volume e mudo passam por um único ponto e só chegam ao libVLC com o áudio estável: o crash raro ao fechar
  vinha de `audio_set_mute` no meio de uma troca de faixa.
- "Desativado" na faixa de áudio silencia sem desligar o fluxo: em links com áudio separado (YouTube) desligar
  o fluxo zerava o relógio e o áudio só voltava após um salto.
- PiP: a janela pequena deixa de pertencer à principal (o Windows esconde janelas "filhas" quando a dona minimiza).
- Segunda abertura: espera a confirmação da instância principal por até 5 s (no Linux, e em PCs lentos, abria
  um segundo app completo).

Pendência
- Atualizações dependem só da conta do GitHub. Recomendado: assinar o manifesto com uma chave Ed25519 guardada
  fora do repositório e conferir no app. A assinatura de código (SignPath) tira o aviso do SmartScreen.

### Abertura e instância única (1.4.5)

- **Entrega antes do Qt** (`refrata/fastlaunch.py`): um segundo `Refrata.exe` tenta entregar os arquivos ao que já está aberto por um pipe nomeado (Windows) ou socket Unix, só com a biblioteca padrão, antes de importar o Qt. Leva ~0,2 s.
- **Uma instância por usuário** (`refrata/single.py`): um `QLockFile` escolhe o primário; o servidor local escuta antes de a interface ser montada e segura os pedidos até `release()`. Cada pedido leva um id, então reenvios não abrem o arquivo duas vezes.
- **Pré-abertura**: `build(preopen=...)` entrega o arquivo ao libVLC antes de carregar o QML; o vídeo fica pronto junto com a janela.
- **Páginas preguiçosas**: só a página visível carrega na abertura; as outras carregam em segundo plano 1,5 s depois.
- **Pré-visualização (espaço)**: o objeto `Shell.Application` do Explorador fica em cache e é aquecido depois da abertura (~125 → ~23 ms por toque).
- **Linha do tempo**: `REFRATA_TRACE=1` imprime cada etapa da abertura (`refrata/trace.py`).
- **Travamento raro**: o aviso de fonte (`qt.text.font.db`) vinha da thread de renderização e passava pelo tratador de mensagens em Python, que precisa da GIL; se a thread da interface estivesse esperando a renderização com a GIL presa, as duas paravam. A categoria agora é silenciada no próprio Qt.

## 3. Rust: o que foi medido

`native/` é um módulo Rust (PyO3 + maturin) com os callbacks de cada quadro (lock/unlock/display) e um pool de
quadros. Ele compila no Windows sem Visual Studio (toolchain `stable-x86_64-pc-windows-gnu`) e é usado com
`REFRATA_NATIVE=1`. Resultado, 4K60, 3 rodadas:

| | Python (padrão) | Rust (`native/`) |
|---|---|---|
| Sem carga | 60 fps, p99 entre quadros 19 ms | 60 fps, p99 24 ms |
| Outra thread Python ocupada | 5 fps | 1–3 fps |

Tirar o Python só da thread do vídeo **não** ganhou: a thread da interface ainda precisa do Python para entregar
cada quadro ao Qt, e o gargalo mudou de lugar. Por isso ele fica desligado por padrão. O ganho real exige que o
quadro vá do Rust direto ao `QVideoSink`, sem Python, e isso é a etapa 3 abaixo.

O que o Rust ganharia no todo: ~0,5 s de abertura, ~60–100 MB de memória, quadros sem GIL, fim do PyInstaller.
O que não ganharia: decodificar e desenhar já são C/C++; o pacote continua com Qt e VLC.

## 4. Caminho do Rust

1. **Medir sempre**: `tools/perf.py` (CPU, memória, quadros) e os bancos de `native/` (seção 6).
2. **Feito**: módulo Rust no app atual e a estrutura de build (maturin, toolchain GNU sem Visual Studio).
3. **Núcleo em Rust com o mesmo QML (cxx-qt)**: os ~4 mil linhas de QML ficam; as ~6 mil de Python viram Rust,
   começando pelo engine (o quadro vai do libVLC ao `QVideoSink` sem passar por Python). Critério de pronto: as
   baterias e2e e robust atuais.
4. **VLC 4** quando estável: o quadro sai já na GPU (`libvlc_video_set_output_callbacks`).

## 5. Linux e macOS

Linux, situação na 1.4.4:
- **Bateria e2e: 50/50 no Ubuntu 24.04** (WSL, Xvfb, libVLC 3.0.20 do sistema). O GitHub Actions roda a
  mesma bateria a cada push (`.github/workflows/linux.yml`).
- Feito: MPRIS2 (teclas de mídia, widget de mídia do GNOME/KDE, playerctl), "mostrar na pasta" pelo gerenciador
  de arquivos, iniciar com o sistema (`~/.config/autostart`), player padrão (`.desktop` + `xdg-mime`), textos sem
  "Windows", ícones Fluent embutidos, prévia do Explorer escondida.
- Falta: pacote (Flatpak recomendado; AppImage como alternativa).

| Peça | Windows | Linux | macOS |
|---|---|---|---|
| Motor | libVLC embutido | libVLC do sistema/Flatpak | libVLC do VLC.app |
| Teclas de mídia | SMTC (winrt) | MPRIS2 (feito) | MPNowPlayingInfoCenter |
| Iniciar com o sistema | Registro `Run` | `~/.config/autostart` (feito) | Login Items |
| Player padrão | Registro | `.desktop` + `xdg-mime` (feito) | `Info.plist` |
| Prévia com Espaço | Explorer | escondida | Quick Look tem o seu |
| Atualizações | instalador + SHA-256 | Flatpak/Flathub | Sparkle |
| Pacote | PyInstaller + Inno + MSIX | Flatpak / AppImage | .app notarizado (conta Apple paga) |

## 6. Medições

App empacotado, Windows 11, CPU de 24 núcleos, dados isolados, biblioteca vazia.

| | 1.4.2 | 1.4.3 |
|---|---|---|
| Parado (CPU por segundo) | 11,5 ms | 1,6 ms |
| 4K60 tocando | 2,18 núcleos | 2,08 núcleos |
| Do clique à primeira imagem | 2–3 s sem nada | 0,16 s (tela de abertura) |

1.4.4, caminho real dos quadros, 4K60 H.264, 3 rodadas:

| | Núcleos | fps |
|---|---|---|
| Software, NV12 (como era) | 1,28 | 60 |
| Software, I420 (1.4.4) | 1,09 | 60 |
| GPU via opção por mídia (1.4.4, padrão) | 0,36 | 60 |

Como repetir: `python tools/perf.py` (app empacotado, dados temporários, nunca a cópia em uso).
