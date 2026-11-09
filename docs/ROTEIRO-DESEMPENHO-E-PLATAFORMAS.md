# Roteiro: desempenho, Rust e outros sistemas

Auditoria de novembro de 2026 (versão 1.4.3). Este documento diz onde o Refrata gasta tempo hoje, o que
já foi corrigido, o que uma reescrita em Rust ganharia de verdade e o caminho para Linux e macOS.

## 1. Onde o tempo vai hoje

Medido num PC com Windows 11 (números do app empacotado na seção 6).

| Etapa | Quem faz | Custo |
|---|---|---|
| Decodificar vídeo | libVLC (C), com GPU quando dá | o maior, já nativo |
| Levar o quadro até a tela | libVLC escreve o NV12 direto na memória de um `QVideoFrame`; o Qt converte para RGB na GPU | 1 cópia (a do próprio libVLC) |
| Avisar que o quadro chegou | 3 chamadas Python por quadro (lock, unlock, display) + 1 sinal para a thread da interface | pequeno, mas passa pelo GIL |
| Interface | Qt Quick (C++), desenho na GPU | nativo |
| Abrir o app | ~0,5 s importando Python, ~1,6 s montando o QML | o QML é C++; o Python é a parte que Rust tiraria |
| Biblioteca | Python (os.scandir), miniaturas pelo libVLC | só no início e ao atualizar |

Conclusão: o que pesa (decodificar e desenhar) já roda em C/C++. O Python fica na cola entre as peças.

## 2. O que esta auditoria corrigiu

Desempenho
- O app consultava o libVLC 8 vezes por segundo para sempre, até parado na bandeja. Agora são 8 por segundo
  só tocando ou carregando, e 1 por segundo no resto.
- O gancho de teclado da prévia com Espaço via todas as teclas do Windows; com o Python ocupado, o teclado
  do sistema podia engasgar. Agora ele só existe enquanto o Explorer ou a área de trabalho estão na frente.
- A busca de duplicados da biblioteca usa só o que a listagem da pasta já traz (nome, tamanho, data), sem abrir
  arquivos (abrir faz o antivírus escanear cada um).
- Tela de abertura do próprio executável: o clique responde em ~0,2 s, mesmo com o app levando segundos para carregar.

Segurança
- Playlists (.m3u/.pls/.xspf) não podem apontar para pastas de rede de outro servidor. Só de conferir um
  caminho `\\servidor\pasta\x.mp3`, o Windows tenta entrar nesse servidor e entrega o hash NTLM da senha.
- O atualizador só baixa o instalador por HTTPS, de hosts do GitHub, sem aceitar redirecionamento para HTTP.
- Legendas baixadas: só HTTPS e no máximo 8 MB. Mensagens entre instâncias: no máximo 4 MB.
- Já estavam certos: SHA-256 do instalador e do yt-dlp, canal entre instâncias só do usuário, chave da
  OpenSubtitles fora do repositório, registro do Windows isolado nos testes.

Pendências conhecidas
- Raramente (1 em ~25 execuções da bateria), fechar o app com a prévia aberta sobre uma música dá um acesso
  inválido no encerramento do libVLC/QtMultimedia. Não afeta o uso (acontece na saída), mas vale investigar.
- Atualizações dependem só da conta do GitHub. Recomendado: assinar o manifesto com uma chave Ed25519
  guardada fora do repositório e conferir no app (ver seção 5). A assinatura de código do Windows
  (SignPath) continua o caminho para tirar o aviso do SmartScreen.

## 3. Rust: o que ganharia de verdade

O Victor sugeriu reescrever em Rust. Ganhos e limites, com honestidade:

Ganharia
- Abertura ~0,5 s mais rápida e ~60–100 MB a menos de memória (sem o interpretador Python).
- Caminho do quadro sem GIL: menos variação de tempo entre quadros em 4K/60 quando a interface está ocupada.
- Um executável menor de manter, sem PyInstaller (e sem os falsos positivos de antivírus que ele atrai).

Não ganharia
- Decodificação, conversão de cor e desenho: já são C/C++ hoje. 4K não fica mais leve por trocar a linguagem.
- Tamanho do pacote: a maior parte são Qt (~190 MB) e o VLC (~150 MB), que continuam.

O ganho grande de vídeo vem do VLC 4 (quando sair estável): ele entrega o quadro já na GPU
(`libvlc_video_set_output_callbacks` com Direct3D 11 ou OpenGL), sem passar pela memória do sistema. Isso
vale com Python ou com Rust.

## 4. Caminho recomendado para o Rust (por etapas, sem parar o app)

1. **Medir sempre** (`tools/perf.py`, seção 6). Toda etapa abaixo tem de mostrar ganho nesses números.
2. **Módulo Rust dentro do app atual** (PyO3 + maturin): o caminho do quadro (lock/unlock/display) e o
   escaneamento da biblioteca. O Python continua orquestrando; dá para comparar lado a lado e voltar atrás.
3. **Núcleo em Rust com o mesmo QML** (cxx-qt): os ~4 mil linhas de QML ficam iguais; as ~6 mil de Python
   viram Rust, módulo por módulo (engine, library, settings, updater...). Aqui somem o Python e o PyInstaller.
4. **VLC 4** quando estável: saída direto na GPU.

Estimativa grosseira: etapa 2 em dias; etapa 3 em semanas a poucos meses, com a bateria de testes atual
(e2e + robust) reaproveitada como critério de pronto.

## 5. Linux (e macOS)

Situação hoje: o Refrata **abre e toca vídeo no Linux** (Ubuntu 24.04 no WSL, PySide6 6.8.1, libVLC 3.0.20 do
sistema). Interface, tema, idiomas e ícones funcionam (a fonte Segoe é do Windows; no Linux entra a Fluent UI
System Icons, licença MIT, embutida com só os 70 ícones usados).

| Peça | Windows | Linux | macOS |
|---|---|---|---|
| Motor | libVLC embutido | libVLC do sistema ou do Flatpak | libVLC do VLC.app |
| Teclas de mídia | SMTC (winrt) | MPRIS2 via D-Bus (QtDBus) | MPNowPlayingInfoCenter |
| Bandeja / iniciar com o sistema | Registro `Run` | `~/.config/autostart/refrata.desktop` | Login Items |
| "Abrir com" / padrão | Registro (assoc.py) | arquivo `.desktop` com `MimeType=` + `xdg-mime` | `Info.plist` |
| Prévia com Espaço | Explorer | não existe equivalente geral; esconder | Quick Look tem o seu |
| Atualizações | instalador + SHA-256 | Flatpak/Flathub cuida | Sparkle |
| Pacote | PyInstaller + Inno Setup + MSIX | Flatpak (recomendado) e AppImage | .app assinado e notarizado (conta Apple paga) |

Próximos passos do Linux, em ordem:
1. Textos que dizem "Windows" (idioma "segue o Windows", Explorer, Apps padrão) passam a depender da plataforma.
2. Esconder o que não existe lá (prévia do Explorer, "Player padrão do Windows") e trocar pelo equivalente.
3. MPRIS2 para as teclas de mídia e o widget de mídia do GNOME/KDE.
4. Pacote Flatpak e CI no GitHub Actions rodando as baterias e2e/robust no Ubuntu (com Xvfb).

## 6. Medições

App empacotado, Windows 11, CPU de 24 núcleos, pasta de dados isolada e biblioteca vazia (com a biblioteca
cheia, as miniaturas dominam os primeiros minutos). "Núcleos" = segundos de CPU por segundo de relógio.

| | 1.4.2 | 1.4.3 |
|---|---|---|
| Parado (CPU por segundo) | 11,5 ms | 1,6 ms |
| Memória parado | 297 MB | 302 MB |
| 1080p30 tocando | 0,58 núcleo, 0 quadros perdidos | 0,61 núcleo, 1 perdido |
| 4K60 tocando | 2,18 núcleos, 16 perdidos | 2,08 núcleos, 5 perdidos |
| Do clique à primeira imagem | ~2–3 s sem nada na tela | 0,16 s (tela de abertura); janela em ~2–3 s |

Decodificação por hardware (4K60 H.264, 5 rodadas de cada, mediana):

| | Núcleos | Quadros em 12 s |
|---|---|---|
| Hardware ligado (padrão) | 1,42 | 953 (2 de 10 rodadas travaram em 236 e 584) |
| Hardware desligado | 1,53 | 954 |

O hardware quase não economiza CPU no caminho atual: o libVLC decodifica na GPU e copia de volta para a
memória do sistema para nos entregar o quadro. Também apareceram travadas só com ele ligado. Próximo passo:
repetir em outras máquinas (notebook com GPU integrada, PC sem placa de vídeo) antes de mudar o padrão. A
solução de fundo é o VLC 4, que entrega o quadro já na GPU.

Como repetir: `python tools/perf.py` sobe o app com `--new-window` ou `--screenshot` numa pasta de dados
temporária (nunca na cópia em uso) e lê CPU e memória por `GetProcessTimes` e `GetProcessMemoryInfo`.
