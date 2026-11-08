"""Builds site/en/index.html and site/es/index.html from the Portuguese page (site/index.html).

    python tools/site_i18n.py

Each entry below maps a piece of the Portuguese page to its English and Spanish wording; the
markup stays the Portuguese page's, so all three versions share one design. Whitespace inside a
piece is matched loosely (the HTML wraps lines). The build fails if a piece is not found or if
Portuguese text is left over in a translated page.
"""
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"

# (pt, en, es)
PIECES = [
    # head
    ('<html lang="pt-BR">', '<html lang="en">', '<html lang="es">'),
    ("<title>Refrata · player de vídeo e música para Windows</title>",
     "<title>Refrata · video and music player for Windows</title>",
     "<title>Refrata · reproductor de vídeo y música para Windows</title>"),
    ('content="Refrata é um player de vídeo e música para Windows, grátis e de código aberto. Usa o motor do VLC, então abre praticamente qualquer arquivo sem instalar codec, com uma interface feita para assistir e prévia com Espaço direto no Explorer."',
     'content="Refrata is a free, open-source video and music player for Windows. It runs on the VLC engine, so it opens almost any file without codec packs, wrapped in an interface made for watching, with Space-bar previews right in File Explorer."',
     'content="Refrata es un reproductor de vídeo y música para Windows, gratuito y de código abierto. Usa el motor de VLC, así que abre casi cualquier archivo sin instalar códecs, con una interfaz pensada para ver y vista previa con la barra espaciadora en el Explorador."'),
    ('content="Refrata · toca tudo que o VLC toca, com outra cara"',
     'content="Refrata · plays everything VLC plays, with a fresh face"',
     'content="Refrata · reproduce todo lo que reproduce VLC, con otra cara"'),
    ('content="Player de vídeo e música para Windows, grátis e de código aberto, feito no Brasil."',
     'content="A free, open-source video and music player for Windows, made in Brazil."',
     'content="Reproductor de vídeo y música para Windows, gratuito y de código abierto, hecho en Brasil."'),
    # nav
    ('aria-label="Refrata, início"', 'aria-label="Refrata, home"', 'aria-label="Refrata, inicio"'),
    ('aria-label="Seções"', 'aria-label="Sections"', 'aria-label="Secciones"'),
    ('<a href="#video">Vídeo</a>', '<a href="#video">Video</a>', '<a href="#video">Vídeo</a>'),
    ('<a href="#rapido">Prévia</a>', '<a href="#rapido">Preview</a>', '<a href="#rapido">Vista previa</a>'),
    ('<a href="#musica">Música</a>', '<a href="#musica">Music</a>', '<a href="#musica">Música</a>'),
    ('<a href="#recursos">Recursos</a>', '<a href="#recursos">Features</a>', '<a href="#recursos">Funciones</a>'),
    ('<a href="#duvidas">Dúvidas</a>', '<a href="#duvidas">FAQ</a>', '<a href="#duvidas">Preguntas</a>'),
    ('aria-label="Idioma"', 'aria-label="Language"', 'aria-label="Idioma"'),
    ('<a class="pill accent" href="#download">Baixar</a>', '<a class="pill accent" href="#download">Download</a>',
     '<a class="pill accent" href="#download">Descargar</a>'),
    # hero
    ('<p class="eyebrow">Grátis e de código aberto</p>', '<p class="eyebrow">Free and open source</p>',
     '<p class="eyebrow">Gratis y de código abierto</p>'),
    ("<h1>Toca tudo.<br><em>Com outra cara.</em></h1>", "<h1>Plays everything.<br><em>Looks brand new.</em></h1>",
     "<h1>Lo reproduce todo.<br><em>Con otra cara.</em></h1>"),
    ("O player de vídeo e música para Windows que roda sobre o motor do VLC. Filme, série, pasta de músicas ou link de transmissão: ele abre praticamente qualquer arquivo sem você caçar codec. Em volta dele, uma interface feita para assistir, não para configurar.",
     "The video and music player for Windows that runs on the VLC engine. Movies, shows, a folder of songs or a stream link: it opens almost any file, no codec hunting required. Around it, an interface made for watching, not for tweaking.",
     "El reproductor de vídeo y música para Windows que funciona con el motor de VLC. Películas, series, una carpeta de canciones o un enlace de streaming: abre casi cualquier archivo sin que tengas que buscar códecs. Y alrededor, una interfaz pensada para ver, no para configurar."),
    ("<strong>Baixar para Windows</strong>", "<strong>Download for Windows</strong>", "<strong>Descargar para Windows</strong>"),
    ("<small data-version-line>Windows 10 e 11 · 64 bits</small>", "<small data-version-line>Windows 10 and 11 · 64-bit</small>",
     "<small data-version-line>Windows 10 y 11 · 64 bits</small>"),
    (">Ver o código</a>", ">View the code</a>", ">Ver el código</a>"),
    ('alt="Player de vídeo do Refrata: o título do filme em letras grandes e os controles em vidro fosco sobre a imagem"',
     'alt="Refrata video player: the movie title in large type and frosted-glass controls over the picture"',
     'alt="Reproductor de vídeo de Refrata: el título de la película en letras grandes y controles de vidrio esmerilado sobre la imagen"'),
    ('aria-label="Formatos suportados"', 'aria-label="Supported formats"', 'aria-label="Formatos compatibles"'),
    # video
    ('<p class="eyebrow">Vídeo</p>', '<p class="eyebrow">Video</p>', '<p class="eyebrow">Vídeo</p>'),
    ('<h2>A cena inteira na tela.<br><span class="dim">Os controles voltam quando você mexe o mouse.</span></h2>',
     '<h2>The whole scene on screen.<br><span class="dim">Controls come back when you move the mouse.</span></h2>',
     '<h2>Toda la escena en pantalla.<br><span class="dim">Los controles vuelven cuando mueves el ratón.</span></h2>'),
    ("Parou o mouse, tudo some e sobra o filme. Mexeu, os controles voltam em vidro fosco, sem tapar a imagem. A barra de tempo mostra para onde você vai antes do clique.",
     "Stop moving the mouse and everything fades, leaving just the movie. Move it and the frosted-glass controls return without covering the picture. The timeline shows where you’ll land before you click.",
     "Deja quieto el ratón y todo desaparece: solo queda la película. Muévelo y los controles de vidrio esmerilado vuelven sin tapar la imagen. La barra de tiempo te muestra adónde vas antes de hacer clic."),
    ("<li>Troque o áudio e a legenda num clique, ou arraste um .srt para a janela</li>",
     "<li>Switch audio and subtitles in one click, or drag an .srt file onto the window</li>",
     "<li>Cambia el audio y los subtítulos con un clic, o arrastra un .srt a la ventana</li>"),
    ("<li>Acerte brilho, contraste, saturação e enquadramento enquanto assiste</li>",
     "<li>Adjust brightness, contrast, saturation and framing while you watch</li>",
     "<li>Ajusta brillo, contraste, saturación y encuadre mientras ves</li>"),
    ("<li>Repita um trecho com <kbd>L</kbd> e deixe a janela sempre no topo com <kbd>T</kbd></li>",
     "<li>Loop a section with <kbd>L</kbd> and keep the window always on top with <kbd>T</kbd></li>",
     "<li>Repite un fragmento con <kbd>L</kbd> y deja la ventana siempre visible con <kbd>T</kbd></li>"),
    ("<li>Velocidade de 0,25× a 4×, avanço quadro a quadro e captura de tela</li>",
     "<li>Speed from 0.25× to 4×, frame-by-frame stepping and snapshots</li>",
     "<li>Velocidad de 0,25× a 4×, avance fotograma a fotograma y capturas</li>"),
    ('alt="Painel Imagem aberto sobre o vídeo, com brilho, contraste, saturação, gama, matiz e enquadramento"',
     'alt="Picture panel open over the video, with brightness, contrast, saturation, gamma, hue and framing"',
     'alt="Panel Imagen abierto sobre el vídeo, con brillo, contraste, saturación, gamma, tono y encuadre"'),
    # quick preview
    ('<p class="eyebrow">Prévia instantânea</p>', '<p class="eyebrow">Instant preview</p>', '<p class="eyebrow">Vista previa al instante</p>'),
    ('<h2>Aperte <span class="keycap">Espaço</span>.<br><span class="dim">Veja antes de abrir.</span></h2>',
     '<h2>Press <span class="keycap">Space</span>.<br><span class="dim">See it before you open it.</span></h2>',
     '<h2>Pulsa <span class="keycap">Espacio</span>.<br><span class="dim">Míralo antes de abrirlo.</span></h2>'),
    ("No Explorer ou na área de trabalho, selecione um vídeo ou uma música e aperte Espaço. A prévia aparece flutuando na hora, e Espaço de novo fecha. As setas passam pelos outros arquivos da pasta, como quem folheia um álbum.",
     "In File Explorer or on the desktop, select a video or a song and press Space. A floating preview appears instantly; press Space again to close it. The arrow keys flip through the rest of the folder, like leafing through an album.",
     "En el Explorador de archivos o en el escritorio, selecciona un vídeo o una canción y pulsa Espacio. La vista previa aparece flotando al instante, y Espacio otra vez la cierra. Las flechas recorren los demás archivos de la carpeta, como quien hojea un álbum."),
    ("<li>Duplo clique abre direto no player completo; se preferir, ative o player rápido também para o duplo clique</li>",
     "<li>Double-click opens straight in the full player; if you prefer, turn on the quick player for double-click too</li>",
     "<li>El doble clic abre directo en el reproductor completo; si lo prefieres, activa el reproductor rápido también para el doble clic</li>"),
    ("<li>Quer a tela cheia? <kbd>Enter</kbd> leva para o app completo, no mesmo segundo</li>",
     "<li>Want the big screen? <kbd>Enter</kbd> takes it to the full app, at the same second</li>",
     "<li>¿Quieres pantalla completa? <kbd>Intro</kbd> lo lleva a la aplicación completa, en el mismo segundo</li>"),
    ("<li>Deixe o Refrata na bandeja e iniciando com o Windows, e tudo abre sem espera</li>",
     "<li>Keep Refrata in the tray and starting with Windows, and everything opens with no wait</li>",
     "<li>Deja Refrata en la bandeja y arrancando con Windows, y todo se abre sin esperas</li>"),
    ('<span class="keycap big press">Espaço</span>', '<span class="keycap big press">Space</span>', '<span class="keycap big press">Espacio</span>'),
    ('alt="Janela de prévia do Refrata flutuando com um vídeo, o nome do arquivo e 3 de 4 na pasta"',
     'alt="Refrata preview window floating with a video, the file name and 3 of 4 in the folder"',
     'alt="Ventana de vista previa de Refrata flotando con un vídeo, el nombre del archivo y 3 de 4 en la carpeta"'),
    # library
    ('<p class="eyebrow">Biblioteca</p>', '<p class="eyebrow">Library</p>', '<p class="eyebrow">Biblioteca</p>'),
    ('<h2>Seus vídeos e músicas,<br><span class="dim">organizados sem você mexer em nada.</span></h2>',
     '<h2>Your videos and music,<br><span class="dim">organized without lifting a finger.</span></h2>',
     '<h2>Tus vídeos y tu música,<br><span class="dim">ordenados sin que muevas un dedo.</span></h2>'),
    ("Na primeira abertura, o Refrata lê suas pastas de Vídeos e Músicas e monta a biblioteca com miniaturas, duração e o que você estava assistindo. Nada é copiado nem enviado para lugar nenhum: ele só lê os arquivos onde já estão.",
     "The first time it opens, Refrata reads your Videos and Music folders and builds the library with thumbnails, lengths and what you were watching. Nothing is copied or sent anywhere: it just reads the files where they already are.",
     "La primera vez que se abre, Refrata lee tus carpetas de Vídeos y Música y monta la biblioteca con miniaturas, duraciones y lo que estabas viendo. No copia ni envía nada a ningún sitio: solo lee los archivos donde ya están."),
    ('alt="Tela inicial do Refrata com o destaque Assistido recentemente e o miniplayer"',
     'alt="Refrata home screen with the Recently watched highlight and the mini player"',
     'alt="Pantalla de inicio de Refrata con el destacado Visto recientemente y el minirreproductor"'),
    ('alt="Grade de vídeos da biblioteca com miniaturas e duração"',
     'alt="Library video grid with thumbnails and lengths"',
     'alt="Cuadrícula de vídeos de la biblioteca con miniaturas y duración"'),
    # features
    ('<p class="eyebrow">Recursos</p>', '<p class="eyebrow">Features</p>', '<p class="eyebrow">Funciones</p>'),
    ('<h2>Tudo que um player sério precisa.<br><span class="dim">Nada escondido em submenu.</span></h2>',
     '<h2>Everything a serious player needs.<br><span class="dim">Nothing buried in submenus.</span></h2>',
     '<h2>Todo lo que necesita un reproductor serio.<br><span class="dim">Nada escondido en submenús.</span></h2>'),
    ("<h3>Abre praticamente tudo</h3>", "<h3>Opens almost anything</h3>", "<h3>Abre casi de todo</h3>"),
    ("MKV, MP4, AVI, HEVC, FLAC, Opus e transmissões HLS e RTSP, com o motor do VLC embutido. Nenhum pacote de codecs para instalar.",
     "MKV, MP4, AVI, HEVC, FLAC, Opus and HLS or RTSP streams, with the VLC engine built in. No codec packs to install.",
     "MKV, MP4, AVI, HEVC, FLAC, Opus y streams HLS y RTSP, con el motor de VLC integrado. Sin paquetes de códecs que instalar."),
    ("<h3>Equalizador de 10 bandas</h3>", "<h3>10-band equalizer</h3>", "<h3>Ecualizador de 10 bandas</h3>"),
    ("18 predefinições, pré-amplificador e volume até 150%, com uma marca no 100% para você não estourar o som sem querer.",
     "18 presets, a preamp and volume up to 150%, with a notch at 100% so you don’t blow out the sound by accident.",
     "18 preajustes, preamplificador y volumen hasta el 150 %, con una marca en el 100 % para que no satures el sonido sin querer."),
    ("<h3>Legenda no tempo certo</h3>", "<h3>Subtitles right on time</h3>", "<h3>Subtítulos a su tiempo</h3>"),
    ("Escolha a faixa de áudio e a legenda, arraste um .srt para a janela e corrija o atraso de cada uma quando estiverem fora de sincronia.",
     "Pick the audio track and subtitles, drag an .srt onto the window and fix the delay of each one when they drift out of sync.",
     "Elige la pista de audio y los subtítulos, arrastra un .srt a la ventana y corrige el retraso de cada uno cuando se desincronicen."),
    ("<h3>Fila e playlists</h3>", "<h3>Queue and playlists</h3>", "<h3>Cola y listas</h3>"),
    ("Tocar a seguir, aleatório e repetir. Abre playlists M3U, PLS e XSPF, e repete um trecho A-B com uma tecla.",
     "Play next, shuffle and repeat. Opens M3U, PLS and XSPF playlists, and loops an A-B section with one key.",
     "Reproducir a continuación, aleatorio y repetir. Abre listas M3U, PLS y XSPF, y repite un fragmento A-B con una tecla."),
    ("<h3>Transmissões pela rede</h3>", "<h3>Network streams</h3>", "<h3>Streams de red</h3>"),
    ("Cole um link HTTP, HLS, RTSP ou RTMP e assista, do jeito que você faria no VLC.",
     "Paste an HTTP, HLS, RTSP or RTMP link and watch, just like you would in VLC.",
     "Pega un enlace HTTP, HLS, RTSP o RTMP y míralo, igual que harías en VLC."),
    ("<h3>Continua de onde parou</h3>", "<h3>Picks up where you left off</h3>", "<h3>Sigue donde lo dejaste</h3>"),
    ("Filmes, podcasts e audiolivros voltam ao ponto em que você parou. Música curta começa do início, como deve ser.",
     "Movies, podcasts and audiobooks return to where you stopped. Short songs start from the top, as they should.",
     "Películas, pódcasts y audiolibros vuelven al punto donde paraste. Las canciones cortas empiezan desde el principio, como debe ser."),
    # music
    ('<p class="eyebrow">Modo música</p>', '<p class="eyebrow">Music mode</p>', '<p class="eyebrow">Modo música</p>'),
    ("<h2>Para ouvir,<br>não só para tocar.</h2>", "<h2>Made for listening,<br>not just playing.</h2>",
     "<h2>Para escuchar,<br>no solo para reproducir.</h2>"),
    ("Um clique toca, outro pausa. A fila vira um carrossel de capas, o fundo assume as cores do disco e o progresso corre em volta do botão de play.",
     "One click plays, another pauses. The queue becomes a carousel of covers, the background takes on the album’s colors and progress runs around the play button.",
     "Un clic reproduce, otro pausa. La cola se convierte en un carrusel de portadas, el fondo toma los colores del disco y el progreso corre alrededor del botón de reproducir."),
    ("<li>Biblioteca com artistas, álbuns, capas e busca</li>", "<li>Library with artists, albums, covers and search</li>",
     "<li>Biblioteca con artistas, álbumes, portadas y búsqueda</li>"),
    ("<li>Carrossel da fila com as capas em 3D</li>", "<li>Queue carousel with 3D covers</li>", "<li>Carrusel de la cola con portadas en 3D</li>"),
    ("<li>Miniplayer que continua com você pelo app</li>", "<li>A mini player that follows you through the app</li>",
     "<li>Minirreproductor que te acompaña por toda la aplicación</li>"),
    ('alt="Biblioteca de músicas do Refrata com capas, artistas e duração"',
     'alt="Refrata music library with covers, artists and lengths"',
     'alt="Biblioteca de música de Refrata con portadas, artistas y duración"'),
    # updates
    ('<p class="eyebrow">Atualizações</p>', '<p class="eyebrow">Updates</p>', '<p class="eyebrow">Actualizaciones</p>'),
    ('<h2>Instale uma vez.<br><span class="dim">As melhorias chegam pelo próprio app.</span></h2>',
     '<h2>Install once.<br><span class="dim">Improvements arrive through the app itself.</span></h2>',
     '<h2>Instálalo una vez.<br><span class="dim">Las mejoras llegan desde la propia aplicación.</span></h2>'),
    ("<h3>Um aviso discreto</h3>", "<h3>A quiet heads-up</h3>", "<h3>Un aviso discreto</h3>"),
    ("Ao abrir, o Refrata confere se saiu versão nova e avisa. Nada é baixado sem o seu clique.",
     "When it opens, Refrata checks for a new version and lets you know. Nothing downloads until you click.",
     "Al abrirse, Refrata comprueba si hay una versión nueva y te avisa. No se descarga nada sin tu clic."),
    ("<h3>Download conferido</h3>", "<h3>Verified download</h3>", "<h3>Descarga verificada</h3>"),
    ("Em <b>Atualizar</b>, ele baixa o instalador e compara o código SHA-256 com o publicado no lançamento. Se não bater, não instala.",
     "Under <b>Update</b>, it downloads the installer and checks its SHA-256 against the one published with the release. If they don’t match, it won’t install.",
     "En <b>Actualizar</b>, descarga el instalador y compara su SHA-256 con el publicado en la versión. Si no coincide, no lo instala."),
    ("<h3>Volta já atualizado</h3>", "<h3>Back, already updated</h3>", "<h3>Vuelve ya actualizado</h3>"),
    ("A instalação roda em segundo plano e o app reabre na versão nova, com a biblioteca e os ajustes do jeito que estavam.",
     "The install runs in the background and the app reopens on the new version, with your library and settings just as they were.",
     "La instalación se hace en segundo plano y la aplicación se vuelve a abrir en la versión nueva, con tu biblioteca y tus ajustes tal como estaban."),
    # download
    ('<p class="eyebrow">Download</p>', '<p class="eyebrow">Download</p>', '<p class="eyebrow">Descarga</p>'),
    ("<h2>Refrata <span data-version>para Windows</span></h2>", "<h2>Refrata <span data-version>for Windows</span></h2>",
     "<h2>Refrata <span data-version>para Windows</span></h2>"),
    ("Grátis de verdade: sem anúncios, sem conta e sem coleta de dados. O motor do VLC já vem dentro, então não precisa instalar mais nada.",
     "Truly free: no ads, no account and no data collection. The VLC engine comes built in, so there’s nothing else to install.",
     "Gratis de verdad: sin anuncios, sin cuenta y sin recopilar datos. El motor de VLC ya viene incluido, así que no hay que instalar nada más."),
    ("<strong>Instalador</strong><small data-size>recomendado</small>", "<strong>Installer</strong><small data-size>recommended</small>",
     "<strong>Instalador</strong><small data-size>recomendado</small>"),
    (">Versão portátil (.zip)</a>", ">Portable version (.zip)</a>", ">Versión portátil (.zip)</a>"),
    ("Windows 10 ou 11, 64 bits ·", "Windows 10 or 11, 64-bit ·", "Windows 10 u 11, 64 bits ·"),
    (">todas as versões</a>", ">all versions</a>", ">todas las versiones</a>"),
    ("<h3>Se o Windows desconfiar</h3>", "<h3>If Windows gets suspicious</h3>", "<h3>Si Windows desconfía</h3>"),
    ("O Refrata ainda não tem assinatura digital, que só é concedida a projetos com mais tempo de estrada. Por isso o navegador e o Windows podem avisar que o arquivo é pouco baixado.",
     "Refrata isn’t digitally signed yet; signing is granted to projects with a longer track record. That’s why your browser and Windows may warn that the file isn’t commonly downloaded.",
     "Refrata todavía no tiene firma digital, que solo se concede a proyectos con más recorrido. Por eso el navegador y Windows pueden avisar de que el archivo se descarga poco."),
    ("<li>No navegador, escolha <b>Manter</b>.</li>", "<li>In your browser, choose <b>Keep</b>.</li>", "<li>En el navegador, elige <b>Conservar</b>.</li>"),
    ("<li>Na tela azul do SmartScreen, clique em <b>Mais informações</b> e depois em <b>Executar assim mesmo</b>.</li>",
     "<li>On the blue SmartScreen dialog, click <b>More info</b>, then <b>Run anyway</b>.</li>",
     "<li>En la pantalla azul de SmartScreen, haz clic en <b>Más información</b> y después en <b>Ejecutar de todas formas</b>.</li>"),
    ("Quer conferir o arquivo? O SHA-256 de cada versão está publicado no lançamento.",
     "Want to check the file? Each version’s SHA-256 is published with the release.",
     "¿Quieres comprobar el archivo? El SHA-256 de cada versión está publicado en la página de la versión."),
    # faq
    ('<p class="eyebrow">Dúvidas</p>', '<p class="eyebrow">FAQ</p>', '<p class="eyebrow">Preguntas</p>'),
    ("<h2>Antes de instalar.</h2>", "<h2>Before you install.</h2>", "<h2>Antes de instalar.</h2>"),
    ("<summary>Preciso ter o VLC instalado?</summary>", "<summary>Do I need VLC installed?</summary>", "<summary>¿Necesito tener VLC instalado?</summary>"),
    ("Não. O motor do VLC já vem dentro do Refrata. Se você tiver o VLC instalado, os dois convivem sem problema, cada um com suas configurações.",
     "No. The VLC engine ships inside Refrata. If you do have VLC installed, the two get along fine, each with its own settings.",
     "No. El motor de VLC ya viene dentro de Refrata. Si tienes VLC instalado, los dos conviven sin problema, cada uno con su configuración."),
    ("<summary>É grátis mesmo? Qual é a pegadinha?</summary>", "<summary>Is it really free? What’s the catch?</summary>",
     "<summary>¿De verdad es gratis? ¿Dónde está el truco?</summary>"),
    ("Não tem. O Refrata é software livre, sob a licença GPL 3.0: sem anúncios, sem conta, sem versão paga e sem coleta de dados. O código inteiro está no GitHub para quem quiser conferir.",
     "There isn’t one. Refrata is free software under the GPL 3.0 license: no ads, no account, no paid tier and no data collection. All the code is on GitHub for anyone to check.",
     "No lo hay. Refrata es software libre con licencia GPL 3.0: sin anuncios, sin cuenta, sin versión de pago y sin recopilar datos. Todo el código está en GitHub para quien quiera revisarlo."),
    ("<summary>Por que o Windows avisa que o arquivo pode ser perigoso?</summary>",
     "<summary>Why does Windows warn that the file might be dangerous?</summary>",
     "<summary>¿Por qué Windows avisa de que el archivo puede ser peligroso?</summary>"),
    ("Porque o instalador ainda não tem assinatura digital, e o Windows desconfia de todo programa novo sem ela. O aviso não indica vírus. Para conferir, compare o SHA-256 do arquivo com o publicado no lançamento, ou gere o instalador você mesmo a partir do código.",
     "Because the installer isn’t digitally signed yet, and Windows is wary of any new program without a signature. The warning doesn’t mean there’s a virus. To check, compare the file’s SHA-256 with the one published with the release, or build the installer yourself from the code.",
     "Porque el instalador todavía no tiene firma digital, y Windows desconfía de cualquier programa nuevo sin ella. El aviso no indica un virus. Para comprobarlo, compara el SHA-256 del archivo con el publicado en la versión, o genera tú mismo el instalador a partir del código."),
    ("<summary>Como deixo o Refrata como player padrão?</summary>", "<summary>How do I make Refrata my default player?</summary>",
     "<summary>¿Cómo pongo Refrata como reproductor predeterminado?</summary>"),
    ("O instalador registra o Refrata para os formatos de vídeo, música e playlist mais comuns. Para torná-lo padrão, clique com o botão direito num arquivo, escolha <b>Abrir com</b>, depois <b>Escolher outro aplicativo</b>, selecione o Refrata e marque para sempre usar.",
     "The installer registers Refrata for the most common video, music and playlist formats. To make it the default, right-click a file, choose <b>Open with</b>, then <b>Choose another app</b>, pick Refrata and tick always use it.",
     "El instalador registra Refrata para los formatos de vídeo, música y listas más comunes. Para que sea el predeterminado, haz clic derecho en un archivo, elige <b>Abrir con</b>, luego <b>Elegir otra aplicación</b>, selecciona Refrata y marca usarla siempre."),
    ("<summary>Qual a diferença entre o instalador e a versão portátil?</summary>",
     "<summary>What’s the difference between the installer and the portable version?</summary>",
     "<summary>¿Qué diferencia hay entre el instalador y la versión portátil?</summary>"),
    ("O instalador cria os atalhos, registra os formatos de arquivo e permite atualizar pelo próprio app. A versão portátil é só descompactar e abrir, bom para testar sem instalar nada.",
     "The installer creates shortcuts, registers file formats and lets you update from inside the app. The portable version just unzips and runs, handy for trying it without installing anything.",
     "El instalador crea los accesos directos, registra los formatos de archivo y permite actualizar desde la propia aplicación. La versión portátil solo hay que descomprimirla y abrirla, ideal para probar sin instalar nada."),
    ("<summary>Em quais idiomas o app está?</summary>", "<summary>What languages does the app speak?</summary>",
     "<summary>¿En qué idiomas está la aplicación?</summary>"),
    ("Em 14: português, inglês, espanhol, francês, alemão, italiano, russo, polonês, turco, indonésio, hindi, chinês, japonês e coreano. Ele segue o idioma do Windows, e dá para trocar em Ajustes.",
     "Fourteen: English, Spanish, Portuguese, French, German, Italian, Russian, Polish, Turkish, Indonesian, Hindi, Chinese, Japanese and Korean. It follows your Windows language, and you can switch in Settings.",
     "Catorce: español, inglés, portugués, francés, alemán, italiano, ruso, polaco, turco, indonesio, hindi, chino, japonés y coreano. Sigue el idioma de Windows, y puedes cambiarlo en Configuración."),
    ("<summary>Encontrei um bug. Como aviso?</summary>", "<summary>I found a bug. How do I report it?</summary>",
     "<summary>He encontrado un error. ¿Cómo aviso?</summary>"),
    ("Abra um relato em", "Open a report on", "Abre un informe en"),
    ("ou escreva para", "or write to", "o escribe a"),
    ("Diga o que você fez, o que esperava e o que aconteceu; se puder, mande um print e a versão, que aparece em Ajustes.",
     "Tell us what you did, what you expected and what happened; if you can, add a screenshot and the version, shown in Settings.",
     "Cuéntanos qué hiciste, qué esperabas y qué pasó; si puedes, añade una captura y la versión, que aparece en Configuración."),
    ("<summary>Como desinstalo?</summary>", "<summary>How do I uninstall it?</summary>", "<summary>¿Cómo lo desinstalo?</summary>"),
    ("Em Configurações do Windows, vá em <b>Aplicativos</b>, procure Refrata e clique em <b>Desinstalar</b>. Isso também remove a opção de iniciar com o Windows, se estiver ligada.",
     "In Windows Settings, go to <b>Apps</b>, find Refrata and click <b>Uninstall</b>. That also removes the start-with-Windows option if it was on.",
     "En Configuración de Windows, ve a <b>Aplicaciones</b>, busca Refrata y haz clic en <b>Desinstalar</b>. También quita el inicio con Windows, si estaba activado."),
    # transparency
    ('<p class="eyebrow">Transparência</p>', '<p class="eyebrow">Transparency</p>', '<p class="eyebrow">Transparencia</p>'),
    ('<h2>Código aberto.<br><span class="dim">Sem coleta. Sem surpresa.</span></h2>',
     '<h2>Open source.<br><span class="dim">No tracking. No surprises.</span></h2>',
     '<h2>Código abierto.<br><span class="dim">Sin rastreo. Sin sorpresas.</span></h2>'),
    ("<h3>Código aberto</h3>", "<h3>Open source</h3>", "<h3>Código abierto</h3>"),
    ("Todo o código está em", "All the code is at", "Todo el código está en"),
    ("sob a GPL 3.0. Os instaladores oficiais saem de um build automatizado e público no GitHub Actions, a partir desse mesmo código.",
     "under the GPL 3.0. Official installers come from a public, automated build on GitHub Actions, from that same code.",
     "bajo la GPL 3.0. Los instaladores oficiales salen de una compilación automática y pública en GitHub Actions, a partir de ese mismo código."),
    ("<h3>Privacidade</h3>", "<h3>Privacy</h3>", "<h3>Privacidad</h3>"),
    ("O Refrata não coleta dados. A única conexão que ele faz por conta própria é consultar a página pública de lançamentos do GitHub para saber se há versão nova, e isso pode ser desligado nos Ajustes.",
     "Refrata collects no data. The only connection it makes on its own is checking GitHub’s public releases page for a new version, and you can turn that off in Settings.",
     "Refrata no recopila datos. La única conexión que hace por su cuenta es consultar la página pública de versiones de GitHub para saber si hay una nueva, y eso se puede desactivar en Configuración."),
    ("<h3>Política de assinatura de código</h3>", "<h3>Code signing policy</h3>", "<h3>Política de firma de código</h3>"),
    ("Os lançamentos ainda não são assinados digitalmente. Cada arquivo publicado traz o SHA-256, que o atualizador do app também confere antes de instalar. Quando houver assinatura, ela será aplicada só aos arquivos gerados pelo build público; nenhum binário montado fora dele será assinado.",
     "Releases aren’t digitally signed yet. Every published file comes with its SHA-256, which the in-app updater also checks before installing. Once there is a signature, it will only be applied to files produced by the public build; no binary assembled outside it will be signed.",
     "Las versiones todavía no están firmadas digitalmente. Cada archivo publicado incluye su SHA-256, que el actualizador de la aplicación también comprueba antes de instalar. Cuando haya firma, solo se aplicará a los archivos generados por la compilación pública; ningún binario hecho fuera de ella se firmará."),
    ("<h3>Quem publica</h3>", "<h3>Who publishes it</h3>", "<h3>Quién lo publica</h3>"),
    ("O Refrata é feito no Brasil pela LucsLabs Studios. Cada lançamento é aprovado numa conta protegida por autenticação em dois fatores.",
     "Refrata is made in Brazil by LucsLabs Studios. Every release is approved from an account protected by two-factor authentication.",
     "Refrata está hecho en Brasil por LucsLabs Studios. Cada versión se aprueba desde una cuenta protegida con autenticación en dos pasos."),
    # footer
    ("Feito no Brasil pela LucsLabs Studios · software livre sob a", "Made in Brazil by LucsLabs Studios · free software under the",
     "Hecho en Brasil por LucsLabs Studios · software libre bajo la"),
    ("Construído sobre o libVLC, da VideoLAN. “VLC” é marca da VideoLAN; o Refrata é um projeto independente.",
     "Built on VideoLAN’s libVLC. “VLC” is a trademark of VideoLAN; Refrata is an independent project.",
     "Construido sobre libVLC, de VideoLAN. «VLC» es una marca de VideoLAN; Refrata es un proyecto independiente."),
    (">Reportar um bug</a>", ">Report a bug</a>", ">Informar de un error</a>"),
]

# app screenshots (site/img/<name>.webp in Portuguese, site/img/<code>/<name>.webp translated)
SHOTS = ("home-videos", "videos-grid", "video-player", "video-adjust", "quick-preview", "music-player", "music-library")

# Portuguese words that must not survive in a translated page (visible text only)
PT_WORDS = re.compile(r"\b(você|não|também|então|vídeos e músicas|arquivo|aperte|baixar|Ajustes|lançamento)\b", re.I)


def loose(text: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in text.split()))


def build():
    src = (SITE / "index.html").read_text(encoding="utf8")
    for idx, code in ((1, "en"), (2, "es")):
        page = src
        for piece in PIECES:
            pat = loose(piece[0])
            page, n = pat.subn(lambda _m, t=piece[idx]: t, page, count=1)
            if not n:
                sys.exit(f"{code}: Portuguese piece not found: {piece[0][:80]!r}")
        # only the root page redirects; the others mark themselves current in the switch
        page = re.sub(r"\s*<!-- lang:redirect -->.*?<!-- /lang:redirect -->", "", page, flags=re.S)
        page = page.replace('data-lang="pt" aria-current="true"', 'data-lang="pt"')
        page = page.replace(f'data-lang="{code}"', f'data-lang="{code}" aria-current="true"')
        # app screenshots exist per language (site/img/<code>/), showing the app in that language
        for shot in SHOTS:
            if not (SITE / "img" / code / f"{shot}.webp").exists():
                sys.exit(f"{code}: missing screenshot img/{code}/{shot}.webp")
            page = page.replace(f'src="/img/{shot}.webp"', f'src="/img/{code}/{shot}.webp"')
        visible = re.sub(r"<script.*?</script>|<[^>]+>", " ", page, flags=re.S)
        # "ajustes" is Spanish too ("tus ajustes")
        left = sorted(set(m.group(0).lower() for m in PT_WORDS.finditer(visible)) - ({"ajustes"} if code == "es" else set()))
        if left:
            sys.exit(f"{code}: Portuguese left over: {left}")
        out = SITE / code / "index.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(page, encoding="utf8", newline="\n")
        print(f"{code}: {out.relative_to(SITE.parent)}")


if __name__ == "__main__":
    build()
