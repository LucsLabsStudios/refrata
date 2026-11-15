<div align="center">

<img src="src/refrata/resources/brand/icon-256.png" alt="Refrata" width="112" height="112">

# Refrata

**Um player moderno de vídeo e música sobre o motor do VLC.**

Todos os codecs, formatos e protocolos que o VLC toca, numa interface desenhada do zero.

[![Última versão](https://img.shields.io/github/v/release/LucsLabsStudios/refrata?label=vers%C3%A3o&color=FF7A2F)](https://github.com/LucsLabsStudios/refrata/releases/latest)
[![Build Windows](https://img.shields.io/github/actions/workflow/status/LucsLabsStudios/refrata/build.yml?branch=main&label=windows)](https://github.com/LucsLabsStudios/refrata/actions/workflows/build.yml)
[![Testes Linux](https://img.shields.io/github/actions/workflow/status/LucsLabsStudios/refrata/linux.yml?branch=main&label=linux)](https://github.com/LucsLabsStudios/refrata/actions/workflows/linux.yml)
[![Licença: GPL-3.0](https://img.shields.io/github/license/LucsLabsStudios/refrata?color=3E91FF&label=licen%C3%A7a)](LICENSE)

[**Baixar**](https://refrata.vercel.app) · [Lançamentos](https://github.com/LucsLabsStudios/refrata/releases) · [Histórico de mudanças](CHANGELOG.md) · [Relatar um bug](https://github.com/LucsLabsStudios/refrata/issues/new/choose) · [English](README.md)

</div>

![O Refrata tocando um filme, com controles de vidro fosco sobre a imagem](docs/images/hero.jpg)

## Por que o Refrata

O VLC toca tudo. O Refrata mantém esse motor e entrega a experiência que se espera de um player
hoje.

- **Toca tudo o que o VLC toca.** O libVLC 3 e todos os módulos vêm dentro do app; não é preciso
  instalar mais nada.
- **Parece um app de hoje.** Tipografia grande, vidro fosco com desfoque em tempo real sobre o
  vídeo, temas claro e escuro, e animações que respeitam a opção do sistema de reduzir movimento.
- **Abre na hora.** Uma janela só, entrega rápida de arquivos vindos do Explorer e prévia no estilo
  QuickLook ao apertar Espaço num arquivo.
- **Atualiza sozinho, com segurança.** A atualização dentro do app confere o SHA-256 e uma
  assinatura Ed25519 da chave do projeto antes de instalar qualquer coisa.
- **Fala o seu idioma.** 14 idiomas, trocados na hora.
- **Testado como produto.** Todo lançamento passa por testes de unidade, uma bateria de ponta a
  ponta que clica em todos os controles, cenários de robustez, limites de tempo de abertura e de
  quadros perdidos, e instalações limpas no Ubuntu e no Debian.

<table>
  <tr>
    <td><img src="site/img/home-videos.webp" alt="Tela inicial com Continue assistindo e vídeos recentes"></td>
    <td><img src="site/img/music-player.webp" alt="Player de música com capa e anel de progresso"></td>
  </tr>
  <tr>
    <td><img src="site/img/video-adjust.webp" alt="Painel de ajustes de imagem sobre um vídeo"></td>
    <td><img src="site/img/quick-preview.webp" alt="Prévia com a barra de espaço sobre o Explorador do Windows"></td>
  </tr>
</table>

## Baixar

| Sistema | Pacote | Observações |
|---|---|---|
| Windows 10/11 (x64) | `Refrata-Setup-<versão>.exe` | Instalação por usuário, sem pedir administrador. Coloca o Refrata em **Abrir com** e em **Apps padrão** para 41 tipos de arquivo, sem tomar o padrão de outro player. Atualiza sozinho. |
| Windows 10/11 (x64) | `Refrata-<versão>-portable.zip` | Descompacte e rode o `Refrata.exe`. Não mexe no Windows; **Ajustes › Abertura rápida › Tornar padrão** registra a cópia quando você quiser. |
| Ubuntu 24.04, Debian 13, Linux Mint 22+ | `refrata_<versão>_amd64.deb` | `sudo apt install ./refrata_<versão>_amd64.deb`. Usa o VLC da distribuição, aparece no menu de aplicativos e no **Abrir com**, e funciona com os controles de mídia (MPRIS). |
| Linux (x86-64) | `Refrata-<versão>-linux-x86_64.tar.gz` | Descompacte e rode. |

Baixe em **[refrata.vercel.app](https://refrata.vercel.app)** ou no
[último lançamento](https://github.com/LucsLabsStudios/refrata/releases/latest). Cada arquivo sai
com o seu `.sha256` e a sua assinatura `.sig`.

> [!NOTE]
> As versões para Windows ainda não têm assinatura digital, então o SmartScreen pode avisar na
> primeira vez. Clique em **Mais informações › Executar assim mesmo**. Veja a
> [política de assinatura](#política-de-assinatura-de-código).

## Funcionalidades

| Área | O que tem |
|---|---|
| **Vídeo** | 4K na resolução nativa com conversão de cor na GPU, decodificação por hardware, brilho/contraste/saturação/gama/matiz, ajustar/preencher/esticar, girar, espelhar, proporção, zoom, desentrelaçar, prévia de cenas na barra de tempo |
| **Áudio** | Volume até 150%, equalizador de 10 bandas com pré-amp e 18 presets, normalizar volume, escolha da saída (alto-falantes, fone, HDMI) |
| **Legendas** | Troca de faixa, arquivos externos (botão ou arrastar), atraso, busca no OpenSubtitles pelo hash do arquivo, tamanho/cor/fundo, codificação |
| **Reprodução** | Fila com reordenar e tocar em seguida, aleatório e repetir, velocidade 0,25×–4×, repetir trecho (A-B), quadro a quadro, capítulos, continuar de onde parou, timer de sono, captura de quadro |
| **Biblioteca** | Varredura das pastas de vídeos e músicas, miniaturas, duração e tags, busca e ordenação |
| **Rede** | YouTube, Vimeo e outros sites pelo yt-dlp, streams HTTP/HLS/RTSP/RTMP/SMB, transmissão para Chromecast |
| **Windows** | Prévia com Espaço no Explorer e na área de trabalho, player rápido, bandeja e iniciar com o Windows, picture-in-picture, sempre no topo, teclas de mídia e painel de mídia do Windows |
| **Linux** | Pacotes `.deb` e `.tar.gz`, teclas e widgets de mídia (MPRIS), **Abrir com**, iniciar com a sessão |
| **Playlists** | M3U/M3U8, PLS, XSPF |
| **Idiomas** | Português, inglês, espanhol, francês, alemão, italiano, russo, polonês, turco, indonésio, hindi, chinês simplificado, japonês, coreano |

<details>
<summary><b>Atalhos de teclado</b></summary>

| Tecla | Ação | Tecla | Ação |
|---|---|---|---|
| `Espaço` | Tocar / pausar | `F` / `F11` | Tela cheia |
| `←` `→` | Voltar / avançar 10 s | `↑` `↓` | Volume |
| `M` | Mudo | `N` / `P` | Próximo / anterior |
| `Shift+N` / `Shift+P` | Próximo / anterior capítulo | `[` `]` `=` | Mais lento / mais rápido / normal |
| `S` | Captura de quadro | `E` | Próximo quadro |
| `L` | Trecho A-B | `T` | Sempre no topo |
| `I` | Informações da mídia | `R` | Girar |
| `Ctrl+P` | Picture-in-picture | `Ctrl+O` | Abrir arquivos |
| `Ctrl+L` | Streams | `Esc` | Sair da tela cheia / fechar |

Todos, menos `Esc` e `F11`, podem ser trocados em **Ajustes › Atalhos de teclado**.
</details>

## Compilar a partir do código

Requer Python 3.12. O libVLC é procurado nesta ordem: embutido no app, `vendor/vlc` (criado pelo
build) e depois um VLC 3 instalado no sistema.

```powershell
git clone https://github.com/LucsLabsStudios/refrata.git
cd refrata
pip install -r requirements.txt -r requirements-dev.txt

python src/refrata/__main__.py                    # abre o app
python src/refrata/__main__.py "C:\filme.mkv"     # abre arquivos, pastas, playlists ou URLs
python tests/unit.py                              # testes de unidade (segundos)
python tests/e2e.py                               # bateria de ponta a ponta (uns 5 minutos)
python tools/build.py                             # pasta do app, zip portátil e instalador
```

`pip install -e .` também funciona e cria o comando `refrata`. Empacotamento, lançamentos,
assinatura e todos os testes estão no [guia de desenvolvimento](docs/development.md) (em inglês).

## Estrutura do projeto

```
refrata/
├── src/refrata/          Pacote do app
│   ├── __main__.py       Ponto de entrada: inicialização, instância única, --selftest
│   ├── engine.py         Reprodução no libVLC: fila, faixas, EQ, capítulos, transmissão, quadros para o Qt
│   ├── library.py        Varredura de pastas, modelos filtráveis, miniaturas
│   ├── updater.py        Atualizações assinadas pelo GitHub Releases
│   ├── …                 Integração com o sistema, ajustes, idiomas, legendas, yt-dlp
│   ├── ui/               Toda a interface em Qt Quick (QML)
│   ├── resources/        Ícones, fontes e imagens da marca usados pelo app
│   └── locales/          Traduções, um JSON por idioma
├── tests/                Testes de unidade, ponta a ponta, robustez, abertura e pacotes
├── tools/                Build, lançamento, assinatura, traduções e identidade visual
├── packaging/windows/    Instalador (Inno Setup) e pacote MSIX (Microsoft Store)
├── native/               Pipeline de quadros opcional em Rust (PyO3, experimental)
├── site/                 refrata.vercel.app
└── docs/                 Guias e notas de projeto
```

## Documentação

| Guia | Conteúdo |
|---|---|
| [Guia de desenvolvimento](docs/development.md) | Rodar pelo código, testes, empacotamento, lançamentos, assinatura das atualizações (em inglês) |
| [Arquitetura](docs/architecture.md) | Como o app é montado e por quê (em inglês) |
| [Assinatura de código](docs/code-signing.md) | Configuração da assinatura e o pedido à SignPath |
| [Microsoft Store](docs/microsoft-store.md) | Pacote MSIX e envio para a Store |
| [Identidade visual](docs/brand.md) | Conceito, paleta e geração dos assets |
| [Roteiro de desempenho](docs/roadmap-performance-and-platforms.md) | Medições, Rust e outros sistemas |

## Segurança

O app só aceita atualizações assinadas com a chave Ed25519 do projeto: nem alguém que tomasse a
conta do GitHub conseguiria mandar um instalador para quem já usa o Refrata. Para relatar uma
vulnerabilidade, veja o [SECURITY.md](SECURITY.md).

## Contribuições

O Refrata é código aberto, mas não aceita pull requests: cada atualização chega automaticamente a
todos os apps instalados, então todo o código deste repositório é escrito pelo mantenedor. Bugs,
ideias e dúvidas são muito bem-vindos nas [Issues](https://github.com/LucsLabsStudios/refrata/issues),
e forks são livres pela GPL. Detalhes no [CONTRIBUTING.md](CONTRIBUTING.md).

## Política de assinatura de código

Os lançamentos ainda não têm assinatura digital (o programa da SignPath Foundation pede mais
adoção pública antes; o projeto vai pedir de novo). Todo lançamento sai com o seu SHA-256, que o
atualizador do app também confere antes de instalar.

- Commits e revisão: [LucsLabsStudios](https://github.com/LucsLabsStudios)
- Aprovação: [LucsLabsStudios](https://github.com/LucsLabsStudios)

Binários assinados só são gerados pelo workflow público do GitHub Actions deste repositório
([`.github/workflows/build.yml`](.github/workflows/build.yml)), e cada pedido de assinatura é
aprovado à mão.

**Privacidade:** este programa não envia nenhuma informação a outros sistemas em rede, a não ser
quando pedido pelo usuário ou por quem o instala ou opera. A verificação opcional de atualizações
só lê os lançamentos públicos deste repositório no GitHub e pode ser desligada em
**Ajustes › Atualizações**.

## Licença

Refrata © LucsLabs Studios é software livre sob a [GNU GPL 3.0](LICENSE). Inclui o libVLC e os
módulos do VLC 3 (VideoLAN, LGPL 2.1+ / GPL 2+) e o Qt for Python (LGPL 3), redistribuídos sem
modificação; veja [`packaging/windows/installer/LICENSE-pt.txt`](packaging/windows/installer/LICENSE-pt.txt).
"VLC" é marca da VideoLAN. O Refrata é um projeto independente, sem vínculo com a VideoLAN.
