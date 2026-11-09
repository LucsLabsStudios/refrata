# Refrata · LucsLabs Studios

Um player moderno construído sobre o **motor do VLC (libVLC)**: todos os codecs, formatos e
protocolos do VLC, com uma interface nova no estilo One UI / Apple TV. Tipografia grande, vidro
fosco com desfoque real sobre o vídeo, navegação em pílula, temas escuro e claro.

![Refrata](assets/brand/hero.jpg)

## Baixar

**[refrata.vercel.app](https://refrata.vercel.app)** · ou direto na página de
[lançamentos](https://github.com/LucsLabsStudios/refrata/releases/latest).

## Instalar (usuário final)

Rode `Refrata-Setup-<versão>.exe`. **Não precisa de Python nem do VLC instalados**: o libVLC e todos
os módulos do VLC 3 vêm embutidos. A instalação é por usuário (sem pedir administrador) e:

- cria o atalho no menu Iniciar (e na área de trabalho, se você marcar);
- registra o Refrata em **"Abrir com"** e em **Configurações › Apps padrão** para 41 tipos de vídeo,
  música e playlist, sem tomar o padrão de outro player à força;
- mantém **uma única janela**: abrir outro arquivo pelo Explorer manda o arquivo para a janela aberta.

Também há uma versão portátil: `Refrata-<versão>-portable.zip` (descompacte e rode `Refrata.exe`). Ela não mexe
no Windows por conta própria; em **Ajustes › Abertura rápida › Player padrão do Windows**, o botão **Tornar padrão**
registra a cópia em "Abrir com" e Apps padrão para o seu usuário (e corrige o caminho se a pasta mudar de lugar).

### Atualizações

A cópia instalada se atualiza sozinha: ao abrir (no máximo a cada 12 h) o Refrata consulta o
último lançamento do GitHub e, se houver versão nova, mostra um aviso. Em **Atualizar** ele baixa
o instalador, confere o SHA-256 publicado no lançamento, roda o setup em modo silencioso e reabre
já na versão nova. Nada é instalado sem o clique. Dá para desligar ou procurar na hora em
**Ajustes › Atualizações**. A versão portátil só avisa e abre o site; a da Store é atualizada pela
própria Store.

Na **Microsoft Store**, o Refrata vem como pacote MSIX assinado pela Microsoft, com as mesmas
associações de arquivo, o comando `refrata` no terminal e desinstalação limpa. Como publicar está
em [docs/STORE.md](docs/STORE.md).

## Desenvolver

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python main.py                       # abre o app
python main.py "C:\filme.mkv"        # abre já reproduzindo (arquivos, pastas, playlists ou URLs)
python main.py --new-window          # ignora a instância única
```

O app procura o libVLC nesta ordem: embutido no pacote → `vendor/vlc` (criado pelo build) → VLC
instalado em Program Files.

## Publicar uma versão

```powershell
# suba a versão em refrata/__init__.py (ex.: 1.2.0), faça o commit e:
python tools/release.py --notes-file notas.md
# sem mudanças no app, só para levar um VLC ou yt-dlp novo (1.2.0 → 1.2.1):
python tools/release.py --bump
```

O `tools/release.py`, em ordem:

1. Atualiza os componentes: o VLC 3 mais recente da VideoLAN (`tools/vlc-version.txt`), o yt-dlp e o
   python-vlc 3 mais recentes do PyPI (`requirements.txt`).
2. Gera o build.
3. Roda as duas baterias de testes no app empacotado (e2e e robustez). Se algo falhar, **nada é
   publicado** e os componentes voltam às versões anteriores.
4. Assina o instalador e o zip com a chave de atualização (`.sig`, Ed25519), faz o commit das versões
   testadas, cria a tag, envia e publica o lançamento no GitHub com o instalador, o `.sha256`, os `.sig`
   e o zip portátil. As notas ganham uma linha para cada componente novo.

### Chave de atualização

Desde a 1.4.5 o app só instala uma atualização assinada pela chave do projeto: alguém que tomasse a
conta do GitHub não conseguiria empurrar um instalador. A chave privada fica só no `.env` (ignorado
pelo git), em `REFRATA_UPDATE_KEY`; a pública está em `refrata/updater.py` (`UPDATE_PUBLIC_KEY`).

```powershell
python tools/update_key.py generate   # uma vez (já feito): cria a chave
python tools/update_key.py public     # mostra a pública da chave do .env
python tools/test_ed25519.py          # confere a implementação com os vetores do RFC 8032
```

**Guarde uma cópia do `.env` fora deste PC** (gerenciador de senhas, pendrive). Sem ela não dá para
publicar versões que os apps instalados aceitem; o `release.py` se recusa a publicar sem a chave.

A partir daí todo app instalado recebe o aviso de atualização e o botão de download do site aponta
para a versão nova. `--keep-versions` publica sem mexer nos componentes; `--skip-build --skip-tests`
reaproveita um `dist/` que você já testou.

## Empacotar

```powershell
python tools/build.py                 # vendor/vlc + PyInstaller + zip portátil + instalador + MSIX
python tools/build.py --no-installer --no-msix   # só a pasta dist/Refrata e o zip
python tools/msix.py                  # só os pacotes MSIX (de teste e, com installer/store.json, o da Store)
python tools/build.py --vlc-zip vlc-3.0.24-win64.zip   # usa um zip do VLC que você já tem
python tools/build.py --system-vlc    # usa o VLC instalado neste PC
```

Requer o Inno Setup 6 para o instalador (`winget install JRSoftware.InnoSetup`). Saídas em `dist/`.

### Motor do VLC sempre atualizado

A versão do libVLC que vai dentro do app está em `tools/vlc-version.txt`. O build baixa o zip oficial
dessa versão da VideoLAN (uma vez, em `build/cache`), confere o SHA-256 publicado por ela e embute o
motor. Assim todo build, aqui ou no GitHub Actions, leva o mesmo motor, não importa qual VLC está
instalado na máquina.

```powershell
python tools/check_updates.py   # motor do VLC, yt-dlp e python-vlc: o que está no app × o mais recente
```

Toda segunda-feira, `.github/workflows/watch-updates.yml` roda essa verificação no GitHub e, se algo
estiver para trás, abre (ou atualiza) uma issue com a etiqueta `atualizacoes`, que chega como
notificação. Não é preciso mexer em nada: o próximo `tools/release.py` (ou `--bump`, para uma versão
só de componentes) leva as versões novas, desde que passem nos testes. Um salto de versão principal
(VLC 4) muda a API do libVLC e exige adaptar o app; ele não entra sozinho, e a verificação avisa.

Entre um lançamento e outro, o próprio app mantém o yt-dlp em dia (`refrata/ytdlp.py`): quando você
abre links de sites, no máximo uma vez por dia, e na hora quando um link falha, ele busca a versão
nova no PyPI, confere o SHA-256 e passa a usá-la se for mais nova que a embutida (em
`%LOCALAPPDATA%\Refrata\yt-dlp`). Uma cópia que não carrega é descartada e o app volta à embutida.
Desliga junto com "Procurar atualizações automaticamente", nos Ajustes.

### Assinatura digital

O build assina o `Refrata.exe`, o instalador e o desinstalador (SHA-256 + carimbo de tempo) quando
uma destas variáveis está definida (detalhes em `tools/sign.py`):

| Variável | Certificado |
|---|---|
| `REFRATA_SIGN_AZURE` + `REFRATA_SIGN_AZURE_DLIB` | Azure Artifact Signing (antigo Trusted Signing) |
| `REFRATA_SIGN_PFX` (+ `REFRATA_SIGN_PFX_PASSWORD`) | arquivo .pfx |
| `REFRATA_SIGN_THUMBPRINT` | certificado no repositório do Windows (token USB, HSM) |
| `REFRATA_SIGN_DEV=1` | certificado de desenvolvimento autoassinado, só para testar o fluxo |

```powershell
$env:REFRATA_SIGN_DEV = "1"
python tools/build.py --skip-build     # reassina a pasta existente e refaz zip e instalador
python tools/sign.py --verify dist\Refrata-Setup-1.0.0.exe
```

O certificado de desenvolvimento não tira o alerta do SmartScreen. Para isso é preciso um
certificado emitido por uma autoridade, ou publicar pela Store, onde a Microsoft assina. Como o
Refrata é código aberto, ele pode ter a assinatura de graça pela **SignPath Foundation** quando
for mais conhecido (o primeiro pedido foi recusado por falta de visibilidade pública): o passo a
passo está em [docs/SIGNING.md](docs/SIGNING.md).

## Funcionalidades

| Área | O que tem |
|---|---|
| Idiomas | 14 idiomas (português, inglês, espanhol, francês, alemão, italiano, russo, polonês, turco, indonésio, hindi, chinês, japonês e coreano), trocados na hora em Ajustes; o automático segue o Windows. Textos em `i18n/*.json`, conferidos com `python tools/i18n.py check` |
| Biblioteca | Varredura das pastas (Vídeos/Músicas por padrão), miniaturas geradas pelo libVLC, duração e tags, busca, ordenação, cache |
| Início | Saudação em "big text", destaque *Continue assistindo*, estatísticas, recentes, adicionados, músicas |
| Player | Vídeo com controles em vidro fosco, título gigante, ±10 s, barra de busca com prévia de tempo, auto-ocultar; imagem até 4K em resolução nativa (NV12 direto no quadro do Qt, conversão de cor na GPU) |
| Faixas | Troca de faixa de áudio e legenda, legenda externa (botão ou arrastar `.srt`), atraso de áudio/legenda, busca de legendas no OpenSubtitles (pelo hash do arquivo), tamanho/cor/fundo da legenda, codificação (Windows-1252, 1250, 1251, GB18030, Shift_JIS…) |
| Capítulos | Marcas na barra de tempo, lista de capítulos, `Shift+N/P` |
| Áudio | Volume até 150 %, mudo, equalizador de 10 bandas + pré-amp + 18 presets, normalizar volume, escolha da saída de áudio (alto-falantes, fone, HDMI) |
| Imagem | Brilho, contraste, saturação, gama, matiz; ajustar / preencher / esticar; girar 90°, espelhar, proporção (16:9, 4:3, 21:9, 1:1), zoom, desentrelaçar |
| Fila | A seguir (painel lateral e página), reordenar, remover, tocar em seguida, aleatório, repetir 1/todos |
| Extras | Velocidade 0,25×–4×, timer de sono, captura de quadro, avanço quadro a quadro, continuar de onde parou, repetir um trecho (A-B), sempre no topo, picture-in-picture (`Ctrl+P`: janelinha flutuante sempre por cima) |
| Abertura rápida | Prévia com Espaço no Explorer e na área de trabalho (estilo QuickLook), duplo clique abre direto no player completo (ou no player rápido, se você ativar nos Ajustes), modo em segundo plano na bandeja e iniciar com o Windows |
| Rede | Links do YouTube, Vimeo e outros sites (yt-dlp), HTTP/HLS/RTSP/RTMP/SMB… pela página Streams; transmitir para Chromecast |
| Playlists | M3U/M3U8, PLS, XSPF |
| Windows | Instância única, "Abrir com", Apps padrão, diálogos nativos de arquivo/pasta, barra de título tingida, teclas de mídia do teclado e painel de mídia do Windows |
| Informações | Codec, resolução, fps, bitrate, canais e quadros descartados de cada arquivo (`I`), com cópia para a área de transferência |
| Robustez | Itens com defeito são pulados automaticamente; aviso se uma mídia não abre; configurações e cache corrompidos são ignorados sem travar; fila e biblioteca com milhares de itens |

Atalhos: `Espaço` play/pausa · `F` tela cheia · `←/→` ±10 s · `↑/↓` volume · `M` mudo ·
`N/P` próximo/anterior · `Shift+N/P` capítulo · `[ ] =` velocidade · `S` captura · `E` quadro a quadro · `L` trecho A-B · `T` sempre no topo ·
`I` informações · `R` girar · `Ctrl+P` picture-in-picture · `Ctrl+O` abrir · `Ctrl+L` streams · `Esc` sair/fechar.
Todos, menos `Esc` e `F11`, podem ser trocados em **Ajustes › Atalhos de teclado** (clique na tecla e aperte a nova).

## Desempenho, Rust e outros sistemas

Auditoria, medições (`tools/perf.py`) e o plano para Rust, Linux e macOS: [docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md](docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md).
O app já abre e toca no Linux a partir do código (PySide6 + libVLC do sistema).

## Identidade visual

O ícone é um prisma de vidro em forma de play que decompõe a luz: *lux* + *labs*, da LucsLabs
Studios. Paleta, regras e como regerar os assets estão em [docs/BRAND.md](docs/BRAND.md).

## Arquitetura

```
main.py               boot, instância única, --selftest
refrata/vlcpath.py    localiza o libVLC (embutido → vendor → sistema)
refrata/engine.py     libVLC: reprodução, fila, faixas, EQ, ajustes, capítulos, Chromecast; vídeo NV12 via vmem → QVideoSink
refrata/websites.py   links de sites (YouTube, Vimeo…) resolvidos com yt-dlp
refrata/subsearch.py  legendas do OpenSubtitles.com (chave em REFRATA_OPENSUBTITLES_KEY no build)
refrata/mediakeys.py  teclas de mídia e painel de mídia do Windows (SMTC, via pywinrt)
refrata/library.py    varredura, modelos filtráveis, miniaturas (thread com libVLC próprio)
refrata/settings.py   preferências + histórico/posições (%LOCALAPPDATA%\Refrata)
refrata/single.py     instância única (QLocalServer por usuário)
refrata/app.py        integração com o Windows (DWM, Explorer, abrir arquivos vindos de outra instância)
refrata/updater.py    atualização pelo GitHub Releases (verificar, baixar, conferir SHA-256, instalar)
refrata/quicklook.py  prévia com Espaço no Explorer (gancho de teclado + automação do Explorer)
refrata/tray.py       bandeja, segundo plano e iniciar com o Windows
qml/                  toda a interface (Qt Quick)
tests/                fixtures sintéticos, bateria E2E, driver dos diálogos nativos do Windows
tools/build.py        empacotamento; tools/msix.py gera os pacotes MSIX; tools/sign.py assina
tools/release.py      publica um lançamento no GitHub (que alimenta site e atualizações)
site/                 site do projeto (Vercel)
tools/test_*.py       testes do instalador e do MSIX
tools/brand/          geração da identidade visual (gpt-image) e dos assets
installer/            script do Inno Setup
```

O vídeo é decodificado pelo libVLC num buffer de CPU e entregue ao Qt como `QVideoFrame`
(com descarte de quadros quando a UI está ocupada). Isso permite desenhar qualquer coisa
por cima do vídeo, inclusive o desfoque ao vivo dos painéis, sem os problemas de janela
nativa do VLC tradicional.

## Testes

```powershell
python tests/e2e.py                          # bateria completa (50 testes, ~5 min)
python tests/e2e.py -k t37,t40               # filtro por nome
dist\Refrata\Refrata.exe --selftest --log r.log --report r.json   # a mesma bateria dentro do pacote
python tests/robust.py                       # robustez: ~40 cenários, cada um num processo do app
python tests/robust.py -k quit,corrupt       # filtro por nome
python tests/robust.py --exe dist\Refrata\Refrata.exe   # a mesma suíte no pacote
python tools/test_installer.py               # instala, testa a cópia instalada, abre via registro, desinstala
python tools/test_msix.py                    # o mesmo para o pacote MSIX (veja docs/STORE.md)
```

A bateria sobe o app real (QML + libVLC) com dados isolados e mídia sintética gerada na hora
(tons WAV, vídeo H.264 com 2 faixas de áudio, legenda, playlists, arquivo corrompido, servidor
HTTP local). Ela testa engine, biblioteca, configurações, playlists, rede, cliques reais de mouse
em todos os controles, teclado, painéis, tela cheia, arrastar-e-soltar, **os diálogos nativos do
Windows** (operados de verdade por `tests/windialogs.py`), a instância única com um segundo
processo real, o cartão Sobre e o encerramento. **Qualquer aviso Qt/QML reprova o teste.**

A suíte de robustez (`tests/robust.py`) roda cada cenário num processo próprio do Refrata, que
sai pelo caminho real de encerramento. Cada um precisa sair com código 0, sem `crash.log`, sem
aviso Qt/QML e com `settings.json` válido:

- **fechar em cada estado:** carregando, tocando, pausado, no fim, modo música, tela cheia, player
  rápido, durante a varredura e as miniaturas, no meio de buscas rápidas, com stream HTTP;
- **estresse:** próximo/anterior sem parar, abrir arquivos durante o carregamento, fila de 1000
  itens, biblioteca de 2620 arquivos, arquivos apagados e renomeados durante o uso, memória em
  200 trocas de faixa;
- **dados corrompidos:** `settings.json` ilegível, vazio, com tipos e formatos errados; cache da
  biblioteca inválido; arquivos de dados somente leitura;
- **formatos** (gerados com o próprio libVLC em `tests/formats.py`): MP4, MOV, MKV, OGV, AVI, TS,
  MPEG-2, HEVC, 4K, vertical, 1366×768, só vídeo, MP3, OGG, Opus, M4A, FLAC, 5.1, WAV mono; legendas
  SRT, VTT, ASS e Latin-1; arquivos vazios, cortados, sem extensão, com extensão errada; nomes com
  acentos, japonês, emoji e caminhos acima de 260 caracteres;
- **recursos:** estilo de legenda (medido nos pixels do quadro), normalizador, saída de áudio,
  capítulos, girar/proporção/zoom, desentrelaçar, informações, transmissão, teclas de mídia,
  links de sites (simulados e um YouTube real), legendas online (API simulada);
- **layout:** os 14 idiomas em todas as páginas, Ajustes e painéis sem texto cortado, e o mesmo em
  150 % de escala com a janela no tamanho mínimo;
- **macaco:** cliques e teclas aleatórios (com semente) por 2 minutos, sem aviso nem travamento.

Dá para usar o PC enquanto a bateria roda. Se outro app roubar o foco, o teste reativa a janela
antes de mandar teclas e avisa no final.

Capturas de tela para conferência visual:

```powershell
python main.py --screenshot out.png --page player --panel eq "C:\video.mp4"
python main.py --screenshot out.png --page settings --panel about
```

Logs: `%LOCALAPPDATA%\Refrata\crash.log` (falhas fatais), `native.log` (mensagens internas do
VLC/ffmpeg) e `stdout.log` (versão empacotada).

## Code signing policy

Releases are not code-signed yet (the SignPath Foundation program asks for more public
adoption first; the project will reapply). Every release ships its SHA-256, which the in-app
updater also verifies before installing.

- Committers and reviewers: [LucsLabsStudios](https://github.com/LucsLabsStudios)
- Approvers: [LucsLabsStudios](https://github.com/LucsLabsStudios)

Signed binaries are built only by the public GitHub Actions workflow in this repository
(`.github/workflows/build.yml`), and every signing request is approved by hand.

Privacy: this program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it. The optional update
check only reads the public GitHub releases of this repository and can be turned off in
**Ajustes › Atualizações**.

## Contribuições

O Refrata é código aberto, mas **não aceita pull requests**: todo o código do repositório oficial é
escrito pelo mantenedor, porque cada atualização chega automaticamente a todos os apps instalados.
Bugs, sugestões e dúvidas são bem-vindos nas [Issues](https://github.com/LucsLabsStudios/refrata/issues),
e forks continuam liberados pela GPL 3.0. Detalhes em [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Refrata © LucsLabs Studios, software livre sob a **GNU GPL 3.0** ([LICENSE](LICENSE)). Inclui o
libVLC e os módulos do VLC 3 (VideoLAN, LGPL 2.1+ / GPL 2+) e o Qt for Python (LGPL 3),
redistribuídos sem modificação. Veja `installer/LICENSE-pt.txt`.
"VLC" é marca da VideoLAN; o Refrata é um projeto independente.
